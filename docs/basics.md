# Basics of Bots

## What is a bot?
In the Attendee platform, a bot is an automated participant that can join virtual meetings across Zoom, Google Meet, and Microsoft Teams to perform various tasks such as recording and transcription.

## Bot Capabilities

1. Recording: Bots can record audio and video from meetings
2. Transcription: Bots can transcribe meeting conversations in real-time
3. Speech: Bots can speak arbitrary audio into the meeting
4. Avatars: Bots can display arbitrary images from their virtual webcam
5. Chat: Bots can record and send chat messages

## Bot States
Bots go through these lifecycle states:

1. Ready: Initial state when bot is created
2. Joining: Bot is attempting to join meeting
3. Joined - Not Recording: Bot has joined but isn't recording
4. Joined - Recording: Bot has joined and is recording
5. Leaving: Bot is leaving the meeting
6. Post Processing: Bot is processing recordings
7. Fatal Error: Bot encountered an unrecoverable error
8. Waiting Room: Bot is in meeting's waiting room
9. Ended: Bot has completed all tasks and recordings and transcripts are available for download
10. Data Deleted: Bot data has been permanently deleted (recordings, transcripts, participants)
11. Scheduled: Bot is scheduled to join at a future time (see scheduled bots documentation)
12. Staged: Bot resources are allocated and ready to join at scheduled time
13. Joined - Recording Paused: Bot has joined and recording is temporarily paused
14. Joining Breakout Room: Bot is moving to a breakout room
15. Leaving Breakout Room: Bot is leaving a breakout room
16. Joined - Recording Permission Denied: Bot has joined but doesn't have permission to record

## Transcription Features

1. Realtime transcription
2. Multiple language support
3. Automatic language detection
4. Speaker identification / Diarization
5. Precise timestamps for each utterance
6. Ability to transcribe using third party providers or from platform closed captions

## Configuration Options
Bots can be configured with:

Transcription Settings
   - Language selection
   - Automatic language detection
   - Provider-specific options

Recording Settings
   - Recording format (Audio and Video MP4 / Audio Only MP3 / No recording)
   - Recording view (Speaker View / Gallery View)
   - Additional files created after the meeting (Audio Only MP3 / Video Only MP4)

Automatic leave settings
   - How long should the bot wait to be let into the meeting before giving up?
   - How long it should be silent before the bot leaves?
   - How long should the bot be the only one in the meeting before it leaves?
   - How long the meeting can last before the bot leaves?

Webhooks
   - Bot state changes
   - Transcript updates

## Recording Files

The `recording_settings.format` field controls what the bot records: `mp4` (video and audio, the default), `mp3` (audio only) or `none` (no recording, transcript only).

For `mp4` recordings, you can also ask for extra files that are created from the recording after the meeting ends:

```json
{
    "recording_settings": {
        "format": "mp4",
        "additional_files": ["audio_only", "video_only"]
    }
}
```

- `audio_only` saves an MP3 of the meeting audio.
- `video_only` saves an MP4 of the video without audio.

The extra files are stored next to the main recording. The recording endpoint returns their URLs in `audio_only_url` and `video_only_url` (these are `null` when the file was not requested), and the bot's page in the dashboard shows download links for them. If creating an extra file fails, the error is logged and the main recording is still saved.

The dashboard's Create Bot form has the same options under Recording and Also save.

## Platform Support
Currently supported platforms:
1. Zoom
2. Google Meet
3. Microsoft Teams

## Data Deletion
You can permanently delete all data associated with a bot, including recordings, transcripts, and participant information. 

To delete bot data, use the `POST /api/v1/bots/{bot_id}/delete_data` endpoint. This action:
- Is irreversible and cannot be undone
- Only works for bots in the `ended` or `fatal_error` states
- Preserves bot metadata for audit purposes
- Moves the bot to the `data_deleted` state

Note: Metadata fields (like bot ID, meeting URL, creation time) are retained for audit purposes even after data deletion.
