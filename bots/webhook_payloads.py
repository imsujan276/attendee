from bots.models import RecordingTranscriptionStates
from bots.serializers import CalendarSerializer, ChatMessageSerializer, ParticipantEventSerializer, RecordingSerializer, ZoomOAuthConnectionSerializer


def chat_message_webhook_payload(chat_message):
    return ChatMessageSerializer(chat_message).data


def utterance_webhook_payload(utterance):
    return {
        "speaker_name": utterance.participant.full_name,
        "speaker_uuid": utterance.participant.uuid,
        "speaker_user_uuid": utterance.participant.user_uuid,
        "speaker_is_host": utterance.participant.is_host,
        "timestamp_ms": utterance.timestamp_ms,
        "duration_ms": utterance.duration_ms,
        "transcription": {"transcript": utterance.transcription.get("transcript")} if utterance.transcription else None,
    }


# Same shape as the Get Bot Transcript and Get Bot Recording endpoints, so the transcript and recording don't need to be fetched separately
def transcript_completed_webhook_payload(recording):
    utterances = recording.utterances.select_related("participant").filter(async_transcription__isnull=True, transcription__isnull=False).order_by("timestamp_ms")
    transcript = [
        {
            "speaker_name": utterance.participant.full_name,
            "speaker_uuid": utterance.participant.uuid,
            "speaker_user_uuid": utterance.participant.user_uuid,
            "speaker_is_host": utterance.participant.is_host,
            "timestamp_ms": utterance.timestamp_ms,
            "duration_ms": utterance.duration_ms,
            "transcription": utterance.transcription,
        }
        for utterance in utterances
        if utterance.transcription.get("transcript", "")
    ]
    return {
        "state": RecordingTranscriptionStates.state_to_api_code(recording.transcription_state),
        "failure_data": recording.transcription_failure_data,
        "recording": RecordingSerializer(recording).data,
        "transcript": transcript,
    }


def participant_event_webhook_payload(participant_event):
    return ParticipantEventSerializer(participant_event).data


def calendar_webhook_payload(calendar):
    serialized_calendar = CalendarSerializer(calendar).data
    return {
        "state": serialized_calendar["state"],
        "connection_failure_data": serialized_calendar["connection_failure_data"],
        "last_successful_sync_at": serialized_calendar["last_successful_sync_at"],
        "last_attempted_sync_at": serialized_calendar["last_attempted_sync_at"],
    }


def zoom_oauth_connection_webhook_payload(zoom_oauth_connection):
    serialized_zoom_oauth_connection = ZoomOAuthConnectionSerializer(zoom_oauth_connection).data
    return {
        "state": serialized_zoom_oauth_connection["state"],
        "connection_failure_data": serialized_zoom_oauth_connection["connection_failure_data"],
        "last_successful_sync_at": serialized_zoom_oauth_connection["last_successful_sync_at"],
        "last_attempted_sync_at": serialized_zoom_oauth_connection["last_attempted_sync_at"],
    }
