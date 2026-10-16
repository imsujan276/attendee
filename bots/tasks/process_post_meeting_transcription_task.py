import logging

from celery import shared_task

from bots.models import Recording, RecordingManager, RecordingTranscriptionStates
from bots.tasks.process_async_transcription_task import create_utterances_for_audio_chunks, queue_utterance_groups_for_transcription

logger = logging.getLogger(__name__)


# Transcribes all the per-participant audio for a recording in one pass after the meeting ends.
# The audio chunks are stitched together in time order, so the transcription provider gets the whole
# conversation as context instead of one short clip at a time. The resulting utterances form the
# recording's regular transcript, the same as utterances transcribed during the meeting.
@shared_task(bind=True, soft_time_limit=3600)
def process_post_meeting_transcription(self, recording_id):
    recording = Recording.objects.get(id=recording_id)

    if recording.transcription_state != RecordingTranscriptionStates.NOT_STARTED or recording.utterances.filter(async_transcription__isnull=True).exists():
        logger.info(f"process_post_meeting_transcription was called for recording {recording_id} but its transcription has already started, skipping")
        return

    if not recording.audio_chunks.exists():
        logger.info(f"process_post_meeting_transcription was called for recording {recording_id} but it has no audio chunks, skipping")
        return

    utterances = create_utterances_for_audio_chunks(recording, async_transcription=None)
    RecordingManager.set_recording_transcription_in_progress(recording)
    queue_utterance_groups_for_transcription(utterances)

    logger.info(f"Queued post meeting transcription of {len(utterances)} utterances for recording {recording_id}")
