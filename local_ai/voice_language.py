"""Conservative language selection from ASR metadata, not phone-number geography."""
ALIASES={'chinese':'zh','mandarin':'zh','zh':'zh','zh-cn':'zh','english':'en','en':'en','en-us':'en','en-gb':'en',
         'japanese':'ja','ja':'ja','ja-jp':'ja','cantonese':'yue','yue':'yue','zh-hk':'yue'}
CANTONESE_MARKERS=('唔','哋','嘅','咗','喺','冇','嚟','幾多','几多','唔該','唔该')


def response_language(transcript):
    languages=transcript.get('language') or []
    if isinstance(languages,str):languages=[languages]
    supported=[ALIASES.get(str(language).lower()) for language in languages]
    selected=next((x for x in reversed(supported) if x),None)
    # Qwen-ASR can label a Cantonese transcription as Chinese. These markers are
    # only a dialect hint; callers can override with an explicit language preference.
    if selected=='zh' and any(marker in transcript.get('text','') for marker in CANTONESE_MARKERS):
        return {'language':'yue','basis':'cantonese_text_hint','needs_confirmation':True}
    if selected is None:
        return {'language':None,'basis':'unsupported_or_unknown','needs_confirmation':True}
    return {'language':selected,'basis':'asr_language','needs_confirmation':False}
