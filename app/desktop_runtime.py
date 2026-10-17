"""Targeted runtime fixes for the desktop PDFMathTranslate integration."""

import asyncio
import concurrent.futures
import functools
import re
import threading
import time
from string import Template

import httpx
import openai
import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed


REQUEST_TIMEOUT = (10, 45)
SDK_TIMEOUT = httpx.Timeout(90, connect=10)
SID_LIFETIME = 8 * 60
_FORMULA = re.compile(r"\{\{v\d+\}\}|\{v\d+\}|<b\d+></b\d+>")
_BING_TAG = re.compile(r"<b\d+></b\d+>")
_NUMBER = r"[+\-−]?\d+(?:[.,]\d+)*(?:[eE][+\-−]?\d+)?(?:[ \t]*[%‰])?"
_MONTHS = ('January February March April May June July August September October November December').split()
_MONTH_NAMES = '|'.join(name + '|' + name[:3] for name in _MONTHS) + '|Sept'
_DATE = rf'\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_NAMES})\.?\s+\d{{4}}|(?:{_MONTH_NAMES})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s+\d{{4}})\b'
_DATE_PATTERN = re.compile(_DATE, re.IGNORECASE)
_BING_PROTECTED = re.compile(
    _FORMULA.pattern + '|' + _DATE + r'|https?://[^\s<>]+'
    + r'|\b[A-Z]{1,8}(?:-?\d+)+(?:[A-Za-z])?\b|'
    + _NUMBER + r'(?:\s*[–—−-]\s*' + _NUMBER + ')?', re.IGNORECASE)


def protected_value(value, target='zh'):
    """Keep a date's day/month/year together instead of hiding bare digits."""
    if target.lower().startswith('zh') and _DATE_PATTERN.fullmatch(value):
        month = next(i for i, name in enumerate(_MONTHS, 1)
                     if re.search(r'\b' + name[:3], value, re.IGNORECASE))
        numbers = re.findall(r'\d+', value)
        return f'{numbers[1]}年{month}月{int(numbers[0])}日'
    return value


def encode_bing_formulas(text, target='zh'):
    """Protect formulas and numerical values using reversible paired tags."""
    markers = {}

    def encode(match):
        index = len(markers)
        tag = f"<b{index}></b{index}>"
        markers[tag] = protected_value(match.group(), target)
        return tag

    return _BING_PROTECTED.sub(encode, text), markers


def restore_bing_formulas(text, markers):
    return _BING_TAG.sub(lambda match: markers.get(match.group(), match.group()), text)


def split_bing_text(text, limit=1000):
    """Keep every source character and keep formula markers in one request."""
    chunks = []
    while len(text) > limit:
        boundary = limit
        for match in _FORMULA.finditer(text):
            if match.start() < boundary < match.end():
                boundary = match.start()
                break
            if match.start() >= boundary:
                break
        prefix = text[:boundary]
        sentence_ends = list(re.finditer(r"[.!?;。！？；](?:\s+|$)", prefix))
        if sentence_ends:
            boundary = sentence_ends[-1].end()
        else:
            word_ends = list(re.finditer(r"\s+", prefix))
            if word_ends:
                boundary = word_ends[-1].end()
        chunks.append(text[:boundary])
        text = text[boundary:]
    if text:
        chunks.append(text)
    return chunks


def is_transient_error(error):
    if not isinstance(error, Exception) or isinstance(
        error,
        (
            asyncio.CancelledError,
            concurrent.futures.CancelledError,
            requests.exceptions.SSLError,
            httpx.LocalProtocolError,
            httpx.UnsupportedProtocol,
        ),
    ):
        return False
    if isinstance(
        error.__cause__,
        (httpx.InvalidURL, httpx.LocalProtocolError, httpx.UnsupportedProtocol),
    ):
        return False
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    if status is not None:
        return status in (408, 429) or 500 <= status <= 599
    return isinstance(
        error,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            openai.APIConnectionError,
            TimeoutError,
            ConnectionError,
        ),
    )


def install_runtime_fixes():
    from pdf2zh import converter
    from pdf2zh.translator import (
        BaseTranslator,
        BingTranslator,
        DeepseekTranslator,
        OpenAITranslator,
        TencentTranslator,
        ZhipuTranslator,
        credential,
        TmtClient,
        TextTranslateRequest,
    )

    if getattr(converter, "_desktop_runtime_fixes_installed", False):
        return

    from pdf_input import install_pdf_input_fix
    install_pdf_input_fix()
    from layout_fixes import install_layout_fixes
    install_layout_fixes()
    from vector_fixes import install_vector_fixes
    install_vector_fixes()

    original_prompt = BaseTranslator.prompt

    @functools.wraps(original_prompt)
    def translation_prompt(self, text, prompt_template=None):
        if prompt_template is not None:
            if isinstance(prompt_template, str):
                prompt_template = Template(prompt_template)
            if not prompt_template.template.strip():
                prompt_template = None
            elif "text" not in prompt_template.get_identifiers():
                prompt_template = Template(
                    "You are a professional translation engine. Output only the translation.\n"
                    "Translate from $lang_in to $lang_out. Keep formula notation {v*} unchanged.\n"
                    "Terminology and additional instructions:\n"
                    + prompt_template.template
                    + "\n\nSource Text: $text\n\nTranslated Text:"
                )
        return original_prompt(self, text, prompt_template)

    BaseTranslator.prompt = translation_prompt

    original_tencent_init = TencentTranslator.__init__

    @functools.wraps(original_tencent_init)
    def tencent_init(self, lang_in, lang_out, model, envs=None, ignore_cache=False, **kwargs):
        self.set_envs(envs)
        BaseTranslator.__init__(self, lang_in, lang_out, model, ignore_cache)
        try:
            cred = credential.DefaultCredentialProvider().get_credential()
        except EnvironmentError:
            cred = credential.Credential(
                self.envs["TENCENTCLOUD_SECRET_ID"],
                self.envs["TENCENTCLOUD_SECRET_KEY"],
            )
        self.client = TmtClient(cred, "ap-beijing")
        self.req = TextTranslateRequest()
        self.req.Source = self.lang_in
        self.req.Target = self.lang_out
        self.req.ProjectId = 0

    TencentTranslator.__init__ = tencent_init

    original_bing_init = BingTranslator.__init__
    original_find_sid = BingTranslator.find_sid

    @functools.wraps(original_bing_init)
    def bing_init(self, *args, **kwargs):
        original_bing_init(self, *args, **kwargs)
        self.session.request = functools.partial(self.session.request, timeout=REQUEST_TIMEOUT)
        self._desktop_sid_lock = threading.Lock()
        self._desktop_sid = None
        self._desktop_sid_expires = 0.0
        self.add_cache_impact_parameters("desktop_bing_full_text", 5)

    def cached_find_sid(self):
        with self._desktop_sid_lock:
            if self._desktop_sid is None or time.monotonic() >= self._desktop_sid_expires:
                self._desktop_sid = original_find_sid(self)
                self._desktop_sid_expires = time.monotonic() + SID_LIFETIME
            return self._desktop_sid

    def bing_translate(self, text):
        def request_translation(chunk):
            url, ig, iid, key, token = self.find_sid()
            response = self.session.post(
                f"{url}ttranslatev3?IG={ig}&IID={iid}",
                data={
                    "fromLang": self.lang_in,
                    "to": self.lang_out,
                    "text": chunk,
                    "token": token,
                    "key": key,
                },
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()[0]["translations"][0]["text"]

        def translate_fragment(fragment):
            content = fragment.strip()
            if not content:
                return fragment
            leading = fragment[: len(fragment) - len(fragment.lstrip())]
            trailing = fragment[len(fragment.rstrip()) :]
            translated = " ".join(request_translation(part) for part in split_bing_text(content))
            return leading + translated + trailing

        def translate_around_markers(original):
            parts = []
            position = 0
            for match in _BING_PROTECTED.finditer(original):
                parts.append(translate_fragment(original[position : match.start()]))
                parts.append(protected_value(match.group(), self.lang_out))
                position = match.end()
            parts.append(translate_fragment(original[position:]))
            return "".join(parts)

        encoded, markers = encode_bing_formulas(text, self.lang_out)
        translations = []
        for chunk in split_bing_text(encoded):
            original = restore_bing_formulas(chunk, markers)
            if _BING_PROTECTED.fullmatch(original.strip()):
                translations.append(original)
                continue
            translated = request_translation(chunk)
            expected = sorted(_BING_TAG.findall(chunk))
            actual = sorted(_BING_TAG.findall(translated))
            if expected != actual:
                translations.append(translate_around_markers(original))
            else:
                translations.append(restore_bing_formulas(translated, markers))
        return " ".join(translations)

    BingTranslator.__init__ = bing_init
    BingTranslator.find_sid = cached_find_sid
    BingTranslator.do_translate = bing_translate

    original_openai_init = OpenAITranslator.__init__

    @functools.wraps(original_openai_init)
    def openai_init(self, *args, **kwargs):
        original_openai_init(self, *args, **kwargs)
        self.client = self.client.with_options(timeout=SDK_TIMEOUT, max_retries=0)

    OpenAITranslator.__init__ = openai_init
    # The shared translation entry owns retries; remove the nested 100-attempt loop.
    OpenAITranslator.do_translate = OpenAITranslator.do_translate.__wrapped__
    ZhipuTranslator.do_translate = OpenAITranslator.do_translate

    original_deepseek_init = DeepseekTranslator.__init__
    DeepseekTranslator.envs = {
        **DeepseekTranslator.envs,
        "DEEPSEEK_MODEL": "deepseek-flash",
    }

    @functools.wraps(original_deepseek_init)
    def deepseek_init(self, *args, **kwargs):
        original_deepseek_init(self, *args, **kwargs)
        self.client = self.client.with_options(base_url="https://api.deepseek.com")
        self.options["extra_body"] = {"thinking": {"type": "disabled"}}
        self.add_cache_impact_parameters("prompt", self.prompt("", self.prompttext))
        self.add_cache_impact_parameters("thinking", "disabled")

    DeepseekTranslator.__init__ = deepseek_init

    # Native text and image/table/chart text all call this shared entry. Keep one
    # three-attempt policy so an OCR timeout receives the same treatment as prose.
    BaseTranslator.translate = retry(
        stop=stop_after_attempt(3), wait=wait_fixed(1),
        retry=retry_if_exception(is_transient_error), reraise=True,
    )(BaseTranslator.translate)

    original_retry = converter.retry

    def bounded_retry(*args, **kwargs):
        # The official converter also decorates its worker. A single outer
        # attempt prevents three shared attempts from multiplying into nine.
        kwargs.update(stop=stop_after_attempt(1), reraise=True)
        return original_retry(*args, **kwargs)

    converter.retry = bounded_retry
    converter._desktop_runtime_fixes_installed = True
