def _parse_words(payload):
    """
    Accepte :
      - string JSON (liste de mots OU objet { words: [...] } OU { data: { words: [...] } })
      - repr Python (Make) équivalents
      - dict/list déjà parsés
      - SRT (fallback : conversion approximative mot-à-mot)
    """
    import json, ast, re

    def _clean(arr):
        out = []
        for w in arr or []:
            try:
                word = str(w.get("word","")).strip()
                st   = float(w.get("start", 0.0))
                en   = float(w.get("end",   st + 0.05))
                # tolère les timestamps en millisecondes
                if st > 1000 or en > 1000:
                    st, en = st / 1000.0, en / 1000.0
                if not word:
                    continue
                if en <= st:
                    en = st + 0.05
                out.append({"word": word, "start": st, "end": en})
            except Exception:
                continue
        out.sort(key=lambda x: (x["start"], x["end"]))
        return out

    def _from_obj(obj):
        # list directe
        if isinstance(obj, list):
            return _clean(obj)
        # dict avec words au top-level
        if isinstance(obj, dict):
            if "words" in obj and isinstance(obj["words"], list):
                return _clean(obj["words"])
            # dict avec data.words (cas OpenAI HTTP data complet)
            data = obj.get("data")
            if isinstance(data, dict) and isinstance(data.get("words"), list):
                return _clean(data["words"])
        return []

    # 1) déjà parsé ?
    if isinstance(payload, (list, dict)):
        words = _from_obj(payload)
        if words: return words

    # 2) string → JSON ou repr python
    if isinstance(payload, str):
        txt = payload.strip()

        # JSON strict
        if txt.startswith("{") or txt.startswith("["):
            try:
                obj = json.loads(txt)
                words = _from_obj(obj)
                if words: return words
            except Exception:
                pass

        # repr Python (Make)
        try:
            obj = ast.literal_eval(txt)
            words = _from_obj(obj)
            if words: return words
        except Exception:
            pass

        # 3) SRT fallback (si jamais on reçoit un vrai SRT)
        if "-->" in txt:
            # conversion bloc→mots approximative
            def _sec(t):
                h,m,s = t.split(":")
                if "," in s:
                    s,ms = s.split(",")
                    return int(h)*3600+int(m)*60+int(s)+int(ms)/1000.0
                return int(h)*3600+int(m)*60+float(s)
            out = []
            for block in txt.replace("\r","").split("\n\n"):
                lines = [l for l in block.split("\n") if l.strip()]
                if len(lines) < 2: 
                    continue
                tline = next((l for l in lines if "-->" in l), None)
                if not tline:
                    continue
                ts, te = [p.strip() for p in tline.split("-->")]
                start, end = _sec(ts), _sec(te)
                text = " ".join(l for l in lines if "-->" not in l and not l.isdigit())
                toks = [t for t in text.split() if t]
                if not toks:
                    continue
                dur = max(1e-3, end-start); step = dur/len(toks)
                for i,t in enumerate(toks):
                    s = start + i*step
                    e = min(end, s+step)
                    out.append({"word": t, "start": s, "end": e})
            return out

        # 4) Regex tolérante (secours)
        pairs = re.findall(
            r"word['\"]?\s*:\s*['\"]([^'^\"]+)['\"].*?start['\"]?\s*:\s*([0-9.]+).*?end['\"]?\s*:\s*([0-9.]+)",
            txt, flags=re.I|re.S
        )
        if pairs:
            return _clean([{"word":w, "start":float(s), "end":float(e)} for (w,s,e) in pairs])

    # rien de valable
    return []
