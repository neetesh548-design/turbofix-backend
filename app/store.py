from app.config import TICKET_STORE

if TICKET_STORE == "sheets":
    from app.store_sheets import (  # noqa: F401
        append_ticket,
        attach_voice_note,
        create_machine,
        get_company_machines,
        get_company_tickets,
        get_machine,
        get_ticket,
        invalidate_machines_cache,
        load_machines,
        next_machine_code,
        next_ticket_id,
        update_ai_fields,
    )
else:
    from app.store_local import (  # noqa: F401
        append_ticket,
        attach_voice_note,
        create_machine,
        get_company_machines,
        get_company_tickets,
        get_machine,
        get_ticket,
        invalidate_machines_cache,
        load_machines,
        next_machine_code,
        next_ticket_id,
        update_ai_fields,
    )
