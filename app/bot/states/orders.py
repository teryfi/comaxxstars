from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    waiting_for_self_amount = State()
    waiting_for_gift_username = State()
    waiting_for_gift_amount = State()
    waiting_for_confirmation = State()
