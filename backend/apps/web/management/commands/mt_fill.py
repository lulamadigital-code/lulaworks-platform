"""Machine-translate the untranslated strings in the locale catalogs via the
Google Cloud Translation API (v2 REST), so activated languages other than the
hand-translated fr/ar/af/pt get a real translation.

The API key is read from the environment (GOOGLE_TRANSLATE_API_KEY in .env) and
never printed. Django placeholders (%(name)s, %s, %%) and inline HTML tags are
protected before translation and restored after; any string whose placeholders
don't survive is left in English rather than shipped broken. Fuzzy flags are
stripped on filled entries. Run `compilemessages` afterwards (this command does).

Usage:
    python manage.py mt_fill --lang es de it nl        # specific languages
    python manage.py mt_fill --all                     # every empty catalog
    python manage.py mt_fill --lang es --limit 50 --dry-run
"""
from __future__ import annotations

import html
import os
import re
import time

import requests
from decouple import config
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

API_URL = "https://translation.googleapis.com/language/translate/v2"
# Our language code -> Google Translate code (only where they differ).
_GOOGLE_CODE = {"zh": "zh-CN"}
_SENTINEL = "{}"  # private-use chars MT leaves alone
_TOKEN_RE = re.compile(r"%\([^)]+\)s|%%|%[sd]|<[^>]+>")


def _protect(text):
    """Replace placeholders/HTML tags with sentinels; return (masked, tokens)."""
    tokens = []

    def repl(m):
        tokens.append(m.group(0))
        return _SENTINEL.format(len(tokens) - 1)

    return _TOKEN_RE.sub(repl, text), tokens


def _restore(text, tokens):
    for i, tok in enumerate(tokens):
        text = text.replace(_SENTINEL.format(i), tok)
    return text


def _placeholders(text):
    return sorted(re.findall(r"%\([^)]+\)s|%%|%[sd]", text))


def _bare(line):
    return re.match(r'^"((?:[^"\\]|\\.)*)"\s*$', line)


def _po_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


class Command(BaseCommand):
    help = "Machine-translate empty catalog entries via Google Cloud Translation."

    def add_arguments(self, parser):
        parser.add_argument("--lang", nargs="+", default=None,
                            help="Language codes to fill (default: all with empty entries).")
        parser.add_argument("--all", action="store_true",
                            help="Fill every language that has untranslated strings.")
        parser.add_argument("--limit", type=int, default=0,
                            help="Max strings to translate per language (0 = no limit).")
        parser.add_argument("--batch", type=int, default=50, help="Strings per API request.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Translate a small sample and print it; write nothing.")

    def handle(self, *args, **opts):
        key = config("GOOGLE_TRANSLATE_API_KEY", default="") or os.environ.get(
            "GOOGLE_TRANSLATE_API_KEY", "")
        if not key:
            raise CommandError(
                "GOOGLE_TRANSLATE_API_KEY is not set. Add it to backend/.env "
                "(GOOGLE_TRANSLATE_API_KEY=your-key) and re-run.")

        base = settings.LOCALE_PATHS[0]
        all_langs = sorted(d for d in os.listdir(base)
                           if os.path.isdir(os.path.join(base, d)) and d != "en")
        langs = opts["lang"] or (all_langs if opts["all"] else None)
        if not langs:
            raise CommandError("Pass --lang <codes...> or --all.")

        for lang in langs:
            po = os.path.join(base, lang, "LC_MESSAGES", "django.po")
            if not os.path.isfile(po):
                self.stderr.write(f"  {lang}: no catalog, skipping")
                continue
            self._fill_language(po, lang, key, opts)

        if not opts["dry_run"]:
            self.stdout.write("Compiling catalogs…")
            call_command("compilemessages", *sum((["-l", l] for l in langs), []))
        self.stdout.write(self.style.SUCCESS("Done."))

    # ── per-language ──────────────────────────────────────────────────────────
    def _fill_language(self, po, lang, key, opts):
        lines = open(po, encoding="utf-8").read().split("\n")
        # Collect untranslated entries: (index_of_msgstr_line, source_text, span_end)
        todo = []
        i = 0
        while i < len(lines):
            if lines[i].startswith("msgid "):
                parts = [re.match(r'^msgid "((?:[^"\\]|\\.)*)"\s*$', lines[i]).group(1)]
                j = i + 1
                while j < len(lines) and _bare(lines[j]):
                    parts.append(_bare(lines[j]).group(1)); j += 1
                src = "".join(parts).replace('\\"', '"').replace("\\\\", "\\")
                # msgstr block starts at j
                if j < len(lines) and lines[j].startswith("msgstr "):
                    mparts = [re.match(r'^msgstr "((?:[^"\\]|\\.)*)"\s*$', lines[j]).group(1)]
                    k = j + 1
                    while k < len(lines) and _bare(lines[k]):
                        mparts.append(_bare(lines[k]).group(1)); k += 1
                    cur = "".join(mparts)
                    if src and not cur:  # untranslated, non-header
                        todo.append((j, k, i, src))
                    i = k; continue
            i += 1

        if opts["limit"]:
            todo = todo[:opts["limit"]]
        if not todo:
            self.stdout.write(f"  {lang}: nothing to translate")
            return

        gcode = _GOOGLE_CODE.get(lang, lang)
        self.stdout.write(f"  {lang}: {len(todo)} strings via Google ({gcode})…")
        translations = {}
        batch = max(1, opts["batch"])
        for start in range(0, len(todo), batch):
            chunk = todo[start:start + batch]
            masked, tokens_list = [], []
            for _, _, _, src in chunk:
                m, tks = _protect(src); masked.append(m); tokens_list.append(tks)
            try:
                got = self._translate(masked, gcode, key)
            except Exception as e:  # noqa: BLE001
                self.stderr.write(f"    batch failed ({e}); stopping {lang}")
                break
            for (jstart, kend, istart, src), out, tks in zip(chunk, got, tokens_list):
                restored = _restore(html.unescape(out), tks)
                if _placeholders(src) != _placeholders(restored):
                    continue  # placeholders broke → leave English
                translations[jstart] = (kend, restored)
            time.sleep(0.2)  # be polite

        if opts["dry_run"]:
            for jstart in list(translations)[:8]:
                _, txt = translations[jstart]
                self.stdout.write(f"    {txt}")
            self.stdout.write(f"  {lang}: would fill {len(translations)}/{len(todo)} (dry-run)")
            return

        # Rebuild the file: replace msgstr blocks + drop fuzzy on filled entries.
        out_lines = []
        i = 0
        while i < len(lines):
            if i in translations:
                kend, txt = translations[i]
                # strip a fuzzy flag in the comment lines just above
                for b in range(len(out_lines) - 1, -1, -1):
                    if out_lines[b].startswith("#, "):
                        fl = [f for f in out_lines[b][3:].split(", ") if f != "fuzzy"]
                        out_lines[b] = ("#, " + ", ".join(fl)) if fl else None
                        if out_lines[b] is None:
                            out_lines.pop(b)
                        break
                    if out_lines[b].startswith("#"):
                        continue
                    break
                out_lines.append(f'msgstr "{_po_escape(txt)}"')
                i = kend; continue
            out_lines.append(lines[i]); i += 1
        open(po, "w", encoding="utf-8").write("\n".join(o for o in out_lines if o is not None))
        self.stdout.write(self.style.SUCCESS(
            f"  {lang}: filled {len(translations)}/{len(todo)}"))

    def _translate(self, texts, target, key):
        r = requests.post(API_URL, params={"key": key}, data={
            "q": texts, "target": target, "source": "en", "format": "text",
        }, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()["data"]["translations"]
        return [t["translatedText"] for t in data]
