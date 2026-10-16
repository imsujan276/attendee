import logging

from celery import shared_task

logger = logging.getLogger(__name__)

from bots.models import RecordingManager, Utterance, WebhookTriggerTypes
from bots.transcription_utils import get_transcription_for_utterance_group, is_retryable_failure
from bots.webhook_payloads import utterance_webhook_payload
from bots.webhook_utils import trigger_webhook


@shared_task(
    bind=True,
    soft_time_limit=3600,
    autoretry_for=(Exception,),
    retry_backoff=True,  # Enable exponential backoff
    max_retries=6,
)
def process_utterance_group_for_async_transcription(self, utterance_ids):
    if len(utterance_ids) == 0:
        logger.warning("process_utterance_group_for_async_transcription was called with no utterance IDs, skipping")
        return

    # The first utterance in the group will be used to keep track of failure data and attempt count
    # The other utterances will only be written to when the utterance group succeeds or fails
    first_utterance = Utterance.objects.get(id=utterance_ids[0])
    utterances = Utterance.objects.filter(id__in=utterance_ids).all()
    if len(utterances) != len(utterance_ids):
        logger.warning(f"process_utterance_group_for_async_transcription was called for utterances {utterance_ids} but some utterances were not found, skipping")
        return
    # Make sure the utterances are in order according to the utterance ids
    utterances = sorted(utterances, key=lambda x: utterance_ids.index(x.id))

    logger.info(f"Processing utterance group for async transcription {utterance_ids}")

    if first_utterance.failure_data:
        logger.info(f"process_utterance_group_for_async_transcription was called for utterances {utterance_ids} but the first utterance has already failed, skipping")
        return

    if first_utterance.transcription is None:
        first_utterance.transcription_attempt_count += 1

        transcriptions, failure_data = get_transcription_for_utterance_group(utterances)

        if failure_data:
            if first_utterance.transcription_attempt_count < 5 and is_retryable_failure(failure_data):
                first_utterance.save()
                raise Exception(f"Retryable failure when transcribing utterances {utterance_ids}: {failure_data}")
            else:
                first_utterance.save()
                # Set the failure data for all the utterances
                for utterance in utterances:
                    utterance.failure_data = failure_data
                    utterance.save()
                logger.info(f"Transcription failed for utterances {utterance_ids}, failure data: {failure_data}")
                finish_post_meeting_transcription_if_done(first_utterance)
                return

        # Loop through the utterances and write the transcription to the utterance
        for utterance in utterances:
            utterance.transcription = transcriptions[utterance.id]
            utterance.save()

        logger.info(f"Transcription complete for utterances {utterance_ids}")

        # Utterances without an async transcription are the recording's regular transcript, transcribed after the meeting
        if first_utterance.async_transcription is None:
            on_post_meeting_utterances_transcribed(utterances)
            finish_post_meeting_transcription_if_done(first_utterance)


def on_post_meeting_utterances_transcribed(utterances):
    recording = utterances[0].recording
    for utterance in utterances:
        # Free up the audio once it's transcribed, unless the user wants to keep it for async transcriptions
        if utterance.audio_chunk and not recording.bot.record_async_transcription_audio_chunks():
            utterance.audio_chunk.clear_audio_data()

        if utterance.transcription.get("transcript"):
            trigger_webhook(
                webhook_trigger_type=WebhookTriggerTypes.TRANSCRIPT_UPDATE,
                bot=recording.bot,
                payload=utterance_webhook_payload(utterance),
            )


def finish_post_meeting_transcription_if_done(utterance):
    if utterance.async_transcription is not None:
        return
    RecordingManager.finish_recording_transcription_if_no_utterances_in_progress(utterance.recording)
