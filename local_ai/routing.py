"""Input metadata only. Semantic intent belongs to the agent, not regex routing."""

# Compatibility labels for pre-v2 callers; v2 discovers actual speech capability.
UNSUPPORTED_AUDIO = '独立语音合成由 generate_audio 能力提供；可用性由实际语音引擎决定。'
CLARIFY_MEDIA = '请说明最终需要的媒体形式。'


def output_contract(job):
    return {'route':'agent','input_mode':'voice' if job.get('audio') else 'text',
            'reply_audio':bool(job.get('audio'))}


def guard_media(job):
    # Retained for older clients. The registry and user task govern each action.
    return None
