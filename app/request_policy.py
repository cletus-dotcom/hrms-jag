from flask.wrappers import Request as FlaskRequest


class HRMSRequest(FlaskRequest):
    """Relax Werkzeug defaults so large forms/uploads don't raise 413.

    Default ``max_form_parts`` is 1000 (Werkzeug); exceeding it yields
    ``RequestEntityTooLarge``. Large ``multipart/form-data`` posts may hit that.
    """

    max_form_parts = 500_000
