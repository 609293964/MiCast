"""Fan out one PCM stream to multiple readers (stereo pair channel split)."""

import asyncio
import contextlib
import logging

logger = logging.getLogger(__name__)


class PCMTee:
    """Copies every PCM chunk from one source reader into N output readers.

    Used by stereo-pair receivers: the RAOP session decodes one stereo PCM
    stream, and each channel pipeline (L/R) consumes its own copy.
    """

    def __init__(self, source: asyncio.StreamReader, outputs: int = 2):
        self._source = source
        self.outputs = [asyncio.StreamReader() for _ in range(outputs)]
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while True:
                chunk = await self._source.read(32768)
                if not chunk:
                    break
                for out in self.outputs:
                    out.feed_data(chunk)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("PCM tee failed")
        finally:
            for out in self.outputs:
                with contextlib.suppress(Exception):
                    out.feed_eof()

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
