def notify_user(user, event):
    if user.notifications.email_enabled:
        send_email(user.email, event.public_reason_json)

    if user.notifications.telegram_enabled:
        send_telegram(user.telegram_chat_id, event.public_reason_json)
