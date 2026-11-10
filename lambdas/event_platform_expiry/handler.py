"""
Lambda: scheduled every few minutes. Cancels unpaid registrations whose payment hold has run out
(30 minutes for card, 7 days for bank transfer / PayPay) and returns their seats. Idempotent.
"""

import logging
import os
from datetime import datetime

import event_mailer
import event_model
import event_registrations as logic
import event_reg_store as store

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


class LazyEvents:
    def __init__(self):
        self.cache = {}

    def get(self, event_id, default=None):
        if event_id not in self.cache:
            self.cache[event_id] = store.get_event(event_id)
        return self.cache[event_id] or default


def _notify(kind, reg, event):
    event_mailer.send(kind, reg, event, {})


def lambda_handler(event, context):
    logic.store, logic.payments, logic.notify = store, None, _notify
    released = logic.expire_holds(datetime.now(event_model.JST), LazyEvents())
    logger.info("released %s expired holds", released)
    return {"released": released}
