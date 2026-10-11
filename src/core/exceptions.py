"""Custom exceptions for the ULB seat reservation bot."""


class BookingError(Exception):
    """Raised when a booking operation fails."""


class NotOpenYet(BookingError):
    """The ULB does not accept bookings for that date (yet)."""
