from app.config import TICKET_STORE

if TICKET_STORE == "sheets":
    from app.users_store_sheets import (  # noqa: F401
        add_user,
        get_company,
        get_user_by_id,
        get_user_by_identifier,
        list_companies,
        next_user_id,
        update_company,
        update_password,
    )
else:
    from app.users_store_local import (  # noqa: F401
        add_user,
        get_company,
        get_user_by_id,
        get_user_by_identifier,
        list_companies,
        next_user_id,
        update_company,
        update_password,
    )
