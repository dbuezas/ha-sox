"""Support for interacting with the SoX music player."""
import logging
import socket

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import media_source
from homeassistant.components.media_player import (
    PLATFORM_SCHEMA,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

_LOGGER = logging.getLogger(__name__)

DOMAIN = "sox"

DEFAULT_NAME = "sox"
DEFAULT_PORT = 7777

SUPPORTED_FEATURES_DEFAULT = (
    MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PLAY_MEDIA
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Inclusive(CONF_HOST, "remote"): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)


def setup_platform(hass, config, add_entities, discover_info):
    """Set up the SoX platform."""
    del discover_info
    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)
    name = config.get(CONF_NAME)

    device = SoXDevice(hass, host, port, name)
    add_entities([device], True)


class SoXDevice(MediaPlayerEntity):
    """Representation of a running SoX."""

    def __init__(self, hass, host, port, name):
        """Initialize the SoX device."""
        self._host = host
        self._port = port
        self._name = name

        self._is_connected = None
        self._is_playing = False
        self._muted = False
        self._muted_volume = None
        self._volume = None

        self.hass = hass
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][name] = {
            "media_id": None,
        }

    @property
    def available(self):
        """Return true if MPD is available and connected."""
        return self._is_connected

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the media state."""
        if self._is_playing:
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def volume_level(self):
        """Return the volume level."""
        return self._volume

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        supported = SUPPORTED_FEATURES_DEFAULT

        if self._volume is not None:
            supported |= (
                MediaPlayerEntityFeature.STOP
                | MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
                | MediaPlayerEntityFeature.VOLUME_MUTE
            )

        return supported

    def mute_volume(self, mute):
        """Mute. Emulated with set_volume_level."""
        if self.volume_level is not None and mute != self._muted:
            if mute:
                self._muted_volume = self.volume_level
                self.set_volume_level(0)
            elif self._muted_volume is not None:
                self.set_volume_level(self._muted_volume)
            self._muted = mute

    def set_volume_level(self, volume):
        """Set volume of media player."""
        self._volume = round(volume, 2)

    def media_play(self):
        """Send play command."""
        _LOGGER.debug("SoX play: %s", self.hass.data[DOMAIN][self._name]["media_id"])
        self._send(self.hass.data[DOMAIN][self._name]["media_id"])

    def media_stop(self):
        """Send stop command."""
        self._send("stop")

    async def async_browse_media(self, media_content_type, media_content_id):
        """Implement the websocket media browsing helper."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Send the play command."""
        del kwargs

        if media_source.is_media_source_id(media_id):
            media_type = MediaType.MUSIC
            play_item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = async_process_play_media_url(self.hass, play_item.url)

        if media_type in [MediaType.MUSIC, MediaType.PLAYLIST]:
            self._send(media_id)
            self.hass.data[DOMAIN][self._name]["media_id"] = media_id
        else:
            _LOGGER.error(
                "Invalid media type %s. Only %s and %s are supported",
                media_type,
                MediaType.MUSIC,
                MediaType.PLAYLIST,
            )

    def _send(self, media_id):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect((self._host, self._port))
                sock.sendall(f"{media_id};{self._volume};".encode())
                output = sock.recv(256).decode("utf-8").rstrip()
                self._is_connected = True
                if "=" in output and ";" in output:
                    output_parsed = dict(x.split("=") for x in output.split(";"))  # type: ignore
                    if "volume" in output_parsed.keys():
                        self._volume = float(output_parsed["volume"])
                    self._is_playing = output_parsed.get("playing") == "True" or False

        except (socket.error, socket.timeout) as err:
            _LOGGER.debug("SoX connection error: %s", err)
            if self._volume is not None:  # For compatibility with old sound server
                self._is_connected = False

    def volume_up(self):
        """Service to send the MPD the command for volume up."""
        if self.volume_level is not None:
            current_volume = self.volume_level

            if current_volume < 1:
                self.set_volume_level(min(current_volume + 0.05, 1))

    def volume_down(self):
        """Service to send the MPD the command for volume down."""
        if self.volume_level is not None:
            current_volume = self.volume_level

            if current_volume > 0:
                self.set_volume_level(max(current_volume - 0.05, 0))

    async def async_update(self):
        """Get the latest data and update the state."""
        if self._is_connected is None or self._volume is not None:
            self._send("")  # For compatibility with old sound server
