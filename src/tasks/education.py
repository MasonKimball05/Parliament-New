"""
Education committee tasks.

reveal_due_bigs — every minute: make timed big/little pairings live
(v3.39.0). See src/big_reveal.py.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='tasks.reveal_due_bigs')
def reveal_due_bigs():
    """Reveal every timed pledge-big pairing whose reveal time has passed."""
    from src.big_reveal import reveal_due_bigs as _reveal_due_bigs
    count = _reveal_due_bigs()
    if count:
        logger.info('Revealed %s pledge big pairing(s).', count)
    return count
