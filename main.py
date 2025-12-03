import telebot
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import json
import time
import threading
from datetime import datetime, timezone, timedelta
import os
import sys
import re


# ==============================================
# НАСТРОЙКА БОТА И ЗАГРУЗКА ТОКЕНА
# ==============================================

# Функция безопасной загрузки токена из файла
def load_token_from_file(filename='token.txt'):
    """
    Загружает токен API из текстового файла.

    Args:
        filename (str): Имя файла с токеном

    Returns:
        str: Токен API

    Raises:
        FileNotFoundError: Если файл не найден
        ValueError: Если файл пустой или слишком большой
    """
    try:
        # Проверяем существование файла
        if not os.path.exists(filename):
            raise FileNotFoundError(f"Файл {filename} не найден")

        # Проверяем размер файла (максимум 1KB)
        if os.path.getsize(filename) > 1024:
            raise ValueError("Файл слишком большой для токена")

        # Читаем файл
        with open(filename, 'r') as file:
            token = file.read().strip()

        # Проверяем, что токен не пустой
        if not token:
            raise ValueError("Токен в файле пустой")

        return token

    except FileNotFoundError as e:
        print(f"Ошибка: {e}")
        print("Создайте файл token.txt с токеном бота в той же папке, что и скрипт")
        sys.exit(1)
    except Exception as e:
        print(f"Ошибка при чтении токена: {e}")
        sys.exit(1)


# Загружаем токен API из файла
API_TOKEN = load_token_from_file('token.txt')

# Инициализация бота
bot = telebot.TeleBot(API_TOKEN)

# ==============================================
# КОНСТАНТЫ И НАСТРОЙКИ
# ==============================================

# Название запрещенного стикерпака
FORBIDDEN_STICKER_SET = "trjufgz_by_stickrubot"

# Базовый путь к директории скрипта
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Файлы для хранения данных
DATA_FILE = os.path.join(BASE_DIR, 'board_data.json')  # Файл с домашними заданиями
MUTED_USERS_FILE = os.path.join(BASE_DIR, 'muted_users.json')  # Файл с заглушенными пользователями

# Список разрешенных чатов для работы бота
TELEGRAM_CHAT_ID = [-1002415770314, -1002425817720, 6066445210, -1002362627260]

# Список администраторов бота
ADMIN_ID = [6066445210]

# ID чата для отладки (куда отправляются debug сообщения)
DEBUG_CHAT_ID = -1002425817720

# ==============================================
# ЗАГРУЗКА И УПРАВЛЕНИЕ ДАННЫМИ
# ==============================================

# Загрузка домашних заданий из JSON файла
try:
    with open(DATA_FILE, 'r', encoding='utf-8') as file:
        file_content = file.read().strip()
        board_data = json.loads(file_content) if file_content else {}
except (FileNotFoundError, json.JSONDecodeError):
    board_data = {}


def save_data():
    """Сохраняет данные домашних заданий в JSON файл."""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as file:
            json.dump(board_data, file, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка при сохранении данных: {e}")
        log_debug_info(f"Критическая ошибка при сохранении данных ДЗ: {e}")


# Загрузка списка заглушенных пользователей
try:
    with open(MUTED_USERS_FILE, 'r', encoding='utf-8') as file:
        muted_users = set(json.load(file))
except (FileNotFoundError, json.JSONDecodeError):
    muted_users = set()


def save_muted_users():
    """Сохраняет список заглушенных пользователей в JSON файл."""
    try:
        with open(MUTED_USERS_FILE, 'w', encoding='utf-8') as file:
            json.dump(list(muted_users), file)
    except Exception as e:
        print(f"Ошибка при сохранении списка заглушенных пользователей: {e}")
        log_debug_info(f"Критическая ошибка при сохранении списка заглушенных пользователей: {e}")


# Состояния пользователей для пошагового добавления ДЗ
# Формат: {user_id: {"state": "waiting_for_subject"|"waiting_for_homework_details",
#                    "subject": "Математика",
#                    "bot_prompt_message_id": int}}
user_states = {}


# ==============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==============================================

def log_debug_info(info: str):
    """
    Отправляет отладочную информацию в указанную группу.

    Args:
        info (str): Текст отладочной информации
    """
    try:
        bot.send_message(DEBUG_CHAT_ID, f"[DEBUG] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {info}")
    except Exception as e:
        print(f"Не удалось отправить отладочную информацию: {e}")


def delete_message_with_delay(chat_id, message_id, delay=300):
    """
    Удаляет сообщение через указанное время.

    Args:
        chat_id: ID чата
        message_id: ID сообщения
        delay (int): Задержка в секундах перед удалением
    """

    def delete():
        try:
            time.sleep(delay)
            bot.delete_message(chat_id, message_id)
        except Exception as e:
            # Не логируем ошибку, если сообщение уже удалено или не может быть удалено
            if "message to delete not found" not in str(e).lower() and "message can't be deleted" not in str(e).lower():
                print(f"Ошибка при удалении сообщения ({chat_id}, {message_id}): {e}")
                log_debug_info(f"Ошибка при удалении сообщения ({chat_id}, {message_id}): {e}")

    threading.Thread(target=delete).start()


def _save_and_confirm_homework(message: Message, subject: str, homework_text: str, photo_file_id: str,
                               user: telebot.types.User):
    """
    Вспомогательная функция для сохранения ДЗ и отправки подтверждения.

    Args:
        message: Объект сообщения
        subject: Название предмета
        homework_text: Текст домашнего задания
        photo_file_id: ID фотографии (если есть)
        user: Объект пользователя
    """
    message_date_unix = message.date
    date_obj_utc = datetime.fromtimestamp(message_date_unix, tz=timezone.utc)
    date_obj_gmt4 = date_obj_utc + timedelta(hours=4)  # Настройте часовой пояс при необходимости (GMT+4)

    months_ru = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
    }
    day = date_obj_gmt4.day
    month_ru = months_ru[date_obj_gmt4.month]
    time_str = date_obj_gmt4.strftime("%H:%M")
    formatted_date = f"{day} {month_ru} в {time_str}"

    user_mention = f"@{user.username}" if user.username else f"User ID: {user.id}"
    full_value_text = f"{homework_text}\n\n(Добавлено {formatted_date} пользователем {user_mention})"

    key_lower = subject.lower()  # Храним предметы в нижнем регистре для удобства поиска
    board_data[key_lower] = {"text": full_value_text, "photo_id": photo_file_id}
    save_data()

    reply_text = f"✅ ДЗ по \"{subject.capitalize()}\" успешно добавлено."
    if photo_file_id:
        reply_text += " (с фото)"

    msg = bot.send_message(message.chat.id, reply_text)
    delete_message_with_delay(msg.chat.id, msg.message_id, delay=15)


def generate_subject_buttons():
    """Генерирует инлайн-кнопки для каждого предмета, по которому есть ДЗ."""
    markup = InlineKeyboardMarkup()
    if board_data:
        # Сортируем предметы по алфавиту для более предсказуемого отображения
        sorted_subjects = sorted(board_data.keys(), key=lambda x: x.lower())
        for key in sorted_subjects:
            markup.add(InlineKeyboardButton(text=key.capitalize(), callback_data=f"get:{key}"))
    return markup


def main_menu():
    """Генерирует основное меню с кнопками действий."""
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Добавить ДЗ", callback_data="set_info"))
    markup.add(InlineKeyboardButton("Удалить ДЗ", callback_data="delete_info"))
    markup.add(InlineKeyboardButton("Показать список ДЗ", callback_data="list"))
    markup.add(InlineKeyboardButton("Что-то не понятно?", url="https://t.me/ShestoyAclassBot/help"))
    return markup


# ==============================================
# ОБРАБОТЧИКИ СОБЫТИЙ
# ==============================================

@bot.message_handler(content_types=['sticker'])
def handle_sticker(message: Message):
    """Обработчик для удаления стикеров из запрещенного стикерпака."""
    if message.sticker and message.sticker.set_name == FORBIDDEN_STICKER_SET:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            log_debug_info(
                f"Удален стикер из запрещенного стикерпака от пользователя {message.from_user.id} (@{message.from_user.username}) в чате {message.chat.id}")
        except Exception as e:
            print(f"Ошибка при удалении стикера: {e}")
            log_debug_info(f"Ошибка при удалении стикера: {e}")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call: CallbackQuery):
    """Обработчик всех инлайн-кнопок."""
    user = call.from_user
    action = call.data
    chat_id = call.message.chat.id
    log_debug_info(f"Пользователь: {user.id} ( @{user.username} ) нажал кнопку: {action} в чате {chat_id}")

    try:
        bot.answer_callback_query(call.id)  # Отвечаем на колбэк, чтобы убрать "часики" на кнопке

        if action == "set_info":
            # Очищаем любое предыдущее состояние для этого пользователя, если оно есть
            if user.id in user_states:
                if user_states[user.id].get("bot_prompt_message_id"):
                    delete_message_with_delay(chat_id, user_states[user.id]["bot_prompt_message_id"], delay=1)
                del user_states[user.id]

            user_states[user.id] = {"state": "waiting_for_subject"}

            # Добавляем кнопку "Отмена"
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Отмена", callback_data="cancel_add_hw"))

            msg = bot.send_message(
                chat_id,
                "Хорошо, чтобы добавить ДЗ, сначала <b>введите название предмета</b>.\n\n"
                "<i>Например: Математика, Английский, \"Изобразительное искусство\"</i>",
                parse_mode="HTML",
                reply_markup=markup
            )
            user_states[user.id][
                "bot_prompt_message_id"] = msg.message_id  # Сохраняем ID сообщения бота для последующего удаления
            delete_message_with_delay(call.message.chat.id, call.message.message_id, delay=1)  # Удаляем предыдущее меню

        elif action == "delete_info":
            msg = bot.send_message(call.message.chat.id,
                                   "Чтобы удалить домашнее задание, введи команду:\n<code>/del предмет</code>\n\nИли, если название предмета из нескольких слов:\n<code>/del \"название предмета\"</code>\n\nГде \"предмет\" - название предмета, которое нужно удалить.",
                                   parse_mode="HTML")
            delete_message_with_delay(msg.chat.id, msg.message_id, delay=120)
            delete_message_with_delay(call.message.chat.id, call.message.message_id, delay=1)

        elif action == "list":
            if board_data:
                markup = generate_subject_buttons()
                if not markup.keyboard:  # Если нет кнопок, значит нет ДЗ
                    msg = bot.send_message(call.message.chat.id, "Список ДЗ пуст.")
                else:
                    msg = bot.send_message(
                        call.message.chat.id,
                        "Выберите предмет для просмотра:",
                        reply_markup=markup
                    )
            else:
                msg = bot.send_message(call.message.chat.id, "Список ДЗ пуст.")
            delete_message_with_delay(msg.chat.id, msg.message_id)  # Удаляем сообщение со списком через некоторое время
            delete_message_with_delay(call.message.chat.id, call.message.message_id,
                                      delay=300)  # Удаляем сообщение с кнопкой "Показать список ДЗ"

        elif action.startswith("get:"):
            subject = action.split(":", 1)[1]

            if subject in board_data:
                homework = board_data[subject]
                text = homework.get("text", "Нет текстового задания.")
                photo_id = homework.get("photo_id")
                full_text = f"ДЗ по \"{subject.capitalize()}\":\n{text}"

                # ID чата, откуда пришел запрос
                origin_chat_id = call.message.chat.id
                # ID пользователя, вызвавшего действие
                user_id = call.from_user.id

                if origin_chat_id == user_id:
                    # Если запрос из ЛС, отправляем только один раз
                    if photo_id:
                        try:
                            bot.send_photo(user_id, photo_id, caption=full_text)
                        except Exception as e_photo:
                            log_debug_info(
                                f"Ошибка отправки фото ДЗ для '{subject}' пользователю {user_id}: {e_photo}. Отправляю текстом.")
                            bot.send_message(user_id, full_text + "\n\n(Не удалось загрузить фото)")
                    else:
                        bot.send_message(user_id, full_text)
                else:
                    # Если запрос из группы, дублируем в ЛС и отправляем в чат с последующим удалением
                    # Отправка в ЛС
                    if photo_id:
                        try:
                            bot.send_photo(user_id, photo_id, caption=full_text)
                        except Exception as e_photo_pm:
                            log_debug_info(
                                f"Ошибка отправки фото ДЗ в ЛС для '{subject}' пользователю {user_id}: {e_photo_pm}. Отправляю текстом.")
                            bot.send_message(user_id, full_text + "\n\n(Не удалось загрузить фото)")
                    else:
                        bot.send_message(user_id, full_text)

                    # Отправка в чат
                    if photo_id:
                        try:
                            sent_to_chat = bot.send_photo(origin_chat_id, photo_id, caption=full_text)
                        except Exception as e_photo_chat:
                            log_debug_info(
                                f"Ошибка отправки фото ДЗ в чат '{origin_chat_id}' для '{subject}': {e_photo_chat}. Отправляю текстом.")
                            sent_to_chat = bot.send_message(origin_chat_id,
                                                            full_text + "\n\n(Не удалось загрузить фото)")
                    else:
                        sent_to_chat = bot.send_message(origin_chat_id, full_text)

                    # Удаление сообщения в чате через 5 минут
                    delete_message_with_delay(origin_chat_id, sent_to_chat.message_id, delay=300)
            else:
                msg = bot.send_message(call.message.chat.id, f"Предмет \"{subject.capitalize()}\" не найден.")
                delete_message_with_delay(msg.chat.id, msg.message_id)

            # Удаляем кнопки выбора предмета после отображения ДЗ (если действие было вызвано в группе)
            if call.message.chat.type != "private":
                delete_message_with_delay(call.message.chat.id, call.message.message_id, delay=1)

        elif action == "cancel_add_hw":
            if user.id in user_states:
                # Удаляем сообщение-подсказку бота, если оно есть
                if user_states[user.id].get("bot_prompt_message_id"):
                    delete_message_with_delay(chat_id, user_states[user.id]["bot_prompt_message_id"], delay=1)
                del user_states[user.id]  # Очищаем состояние пользователя
                msg = bot.send_message(chat_id, "❌ Добавление домашнего задания отменено.")
                delete_message_with_delay(msg.chat.id, msg.message_id, delay=10)
            else:
                msg = bot.send_message(chat_id, "Нет активной операции добавления ДЗ для отмены.")
                delete_message_with_delay(msg.chat.id, msg.message_id, delay=10)
            delete_message_with_delay(call.message.chat.id, call.message.message_id,
                                      delay=1)  # Удаляем само сообщение с кнопкой "Отмена"

    except Exception as e:
        print(f"Ошибка при обработке callback: {e}")
        log_debug_info(f"Ошибка при обработке callback ({action}) от {user.id}: {e}")
        try:
            bot.send_message(call.message.chat.id,
                             "К сожалению, произошла ошибка.\n\n"
                             "Чтобы её исправить:\n"
                             "1. Перейдите в личные сообщения с ботом: @ShestoyAclassBot\n"
                             "2. Напишите ему команду /start (НЕ БЛОКИРУЙТЕ БОТА / НЕ УДАЛЯЙТЕ ЧАТ С НИМ)\n"
                             "3. Вернитесь сюда и повторите действие.\n\n"
                             "Если бот все еще не работает, то обратитесь к @NotReDate\n")
        except:
            pass


@bot.message_handler(commands=['msgu'])
def cmd_msgu(message: Message):
    """Команда администратора для отправки сообщения в любую группу по ID."""
    user = message.from_user
    log_debug_info(f"Пользователь: {user.id} ( @{user.username} ) использовал команду: {message.text}")
    if user.id not in ADMIN_ID:
        msg = bot.reply_to(message, "У вас нет прав для использования этой команды.")
        delete_message_with_delay(message.chat.id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return

    args = message.text.split(' ', 2)
    if len(args) < 3:  # Ожидаем /msgu <chat_id> <text>
        msg = bot.reply_to(message,
                           "Неверный формат. Используйте: <code>/msgu &lt;ID группы&gt; &lt;текст сообщения&gt;</code>",
                           parse_mode="HTML")
        delete_message_with_delay(message.chat.id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return
    else:
        try:
            group_id_str = args[1]
            # Проверяем, является ли group_id_str числом, возможно с минусом впереди
            if not (group_id_str.startswith('-') and group_id_str[1:].isdigit() or group_id_str.isdigit()):
                raise ValueError("ID группы должен быть числом.")

            group_id = int(group_id_str)
            text_to_send = args[2]
            bot.send_message(group_id, text_to_send)
            sent_msg = bot.reply_to(message, f"Сообщение отправлено в группу {group_id}.")
            delete_message_with_delay(message.chat.id, message.message_id)
            delete_message_with_delay(sent_msg.chat.id, sent_msg.message_id)

        except ValueError as ve:
            msg = bot.reply_to(message, f"Ошибка в ID группы: {ve}")
            delete_message_with_delay(message.chat.id, message.message_id)
            delete_message_with_delay(msg.chat.id, msg.message_id)
        except Exception as e:
            msg = bot.reply_to(message, f"Ошибка при отправке сообщения: {str(e)}")
            delete_message_with_delay(message.chat.id, message.message_id)
            delete_message_with_delay(msg.chat.id, msg.message_id)
            log_debug_info(f"Ошибка в /msgu: {e}")


@bot.message_handler(
    func=lambda message: user_states.get(message.from_user.id, {}).get("state") == "waiting_for_subject",
    content_types=['text'])
def handle_subject_input(message: Message):
    """Обработчик для получения названия предмета после нажатия кнопки 'Добавить ДЗ'."""
    user = message.from_user
    chat_id = message.chat.id
    current_state = user_states.get(user.id, {})

    log_debug_info(
        f"Пользователь: {user.id} ( @{user.username} ) ввел предмет: {message.text} в чате {chat_id} (по кнопке)")

    if chat_id not in TELEGRAM_CHAT_ID and chat_id != DEBUG_CHAT_ID:
        msg = bot.reply_to(message, "Эта функция доступна только в настроенных рабочих чатах.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        if user.id in user_states:
            del user_states[user.id]  # Очищаем состояние при отсутствии прав
        return

    if user.id in muted_users:
        msg = bot.reply_to(message, "Вы не можете использовать эту команду, так как вы заглушены.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        if user.id in user_states:
            del user_states[user.id]  # Очищаем состояние при отсутствии прав
        return

    subject = message.text.strip()
    if not subject:
        msg = bot.reply_to(message, "Название предмета не может быть пустым. Пожалуйста, введите название предмета.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id, delay=15)
        # Состояние не меняем, пользователь должен ввести корректный предмет
        return

    # Удаляем предыдущее сообщение-подсказку от бота
    if current_state.get("bot_prompt_message_id"):
        try:
            bot.delete_message(chat_id, current_state["bot_prompt_message_id"])
        except Exception as e:
            log_debug_info(f"Не удалось удалить старое сообщение-подсказку для {user.id}: {e}")

    user_states[user.id]["subject"] = subject
    user_states[user.id]["state"] = "waiting_for_homework_details"

    # Добавляем кнопку "Отмена" к новому сообщению-подсказке
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Отмена", callback_data="cancel_add_hw"))

    msg = bot.send_message(
        chat_id,
        f"Отлично, предмет <b>'{subject.capitalize()}'</b>.\nТеперь <b>отправьте само домашнее задание</b>. Это может быть:\n\n"
        "- <b>Текст</b>\n"
        "- <b>Фото с подписью</b> (подпись будет текстом ДЗ)",
        parse_mode="HTML",
        reply_markup=markup
    )
    user_states[user.id]["bot_prompt_message_id"] = msg.message_id

    delete_message_with_delay(chat_id, message.message_id, delay=1)  # Удаляем сообщение пользователя с предметом


@bot.message_handler(
    func=lambda message: user_states.get(message.from_user.id, {}).get("state") == "waiting_for_homework_details",
    content_types=['text', 'photo'])
def handle_homework_details_input(message: Message):
    """Обработчик для получения деталей ДЗ (текст или фото) после ввода предмета."""
    user = message.from_user
    chat_id = message.chat.id
    current_state = user_states.get(user.id, {})

    log_debug_info(
        f"Пользователь: {user.id} ( @{user.username} ) ввел ДЗ: {message.text or message.caption} (тип: {message.content_type}) в чате {chat_id} (по кнопке)")

    if chat_id not in TELEGRAM_CHAT_ID and chat_id != DEBUG_CHAT_ID:
        msg = bot.reply_to(message, "Эта функция доступна только в настроенных рабочих чатах.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        if user.id in user_states:
            del user_states[user.id]
        return

    if user.id in muted_users:
        msg = bot.reply_to(message, "Вы не можете использовать эту команду, так как вы заглушены.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        if user.id in user_states:
            del user_states[user.id]
        return

    subject = current_state.get("subject")
    if not subject:  # Это не должно произойти при корректном потоке, но для безопасности
        msg = bot.reply_to(message,
                           "Произошла ошибка: предмет не был указан. Пожалуйста, начните добавление ДЗ заново.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        if user.id in user_states:
            del user_states[user.id]
        return

    homework_text = ""
    photo_file_id = None

    if message.content_type == 'text':
        homework_text = message.text.strip()
    elif message.content_type == 'photo':
        homework_text = message.caption.strip() if message.caption else ""
        if message.photo:
            photo_file_id = message.photo[-1].file_id

    if not homework_text and not photo_file_id:
        msg = bot.reply_to(message,
                           "Текст задания не может быть пустым, если не прикреплена фотография. Пожалуйста, отправьте текст или фото с подписью.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id, delay=15)
        # Состояние не меняем, пользователь должен предоставить корректные детали
        return

    # Если предоставлено только фото, используем заглушку для текста
    if not homework_text and photo_file_id:
        homework_text = "(см. фото)"

    # Удаляем предыдущее сообщение-подсказку от бота
    if current_state.get("bot_prompt_message_id"):
        try:
            bot.delete_message(chat_id, current_state["bot_prompt_message_id"])
        except Exception as e:
            log_debug_info(f"Не удалось удалить старое сообщение-подсказку (ДЗ) для {user.id}: {e}")

    _save_and_confirm_homework(message, subject, homework_text, photo_file_id, user)

    delete_message_with_delay(chat_id, message.message_id, delay=1)  # Удаляем сообщение пользователя с ДЗ
    if user.id in user_states:
        del user_states[user.id]  # Очищаем состояние после успешного добавления


@bot.message_handler(commands=['set'], content_types=['text', 'photo'])
def set_homework(message: Message):
    """Обработчик команды /set для добавления ДЗ (текстом или фото с подписью)."""
    user = message.from_user
    chat_id = message.chat.id

    log_debug_info(
        f"Пользователь: {user.id} ( @{user.username} ) использовал команду /set в чате {chat_id}. Тип контента: {message.content_type}. Текст/Подпись: {message.text or message.caption}")

    if user.id in muted_users:
        msg = bot.reply_to(message, "Вы не можете использовать эту команду, так как вы заглушены.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return

    if chat_id not in TELEGRAM_CHAT_ID and chat_id != DEBUG_CHAT_ID:
        msg = bot.reply_to(message, "Эта команда доступна только в настроенных рабочих чатах.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return

    try:
        photo_file_id = None
        command_text = ""

        if message.content_type == 'photo':
            if message.caption and message.caption.lower().startswith('/set '):
                command_text = message.caption.strip()
                if message.photo:
                    photo_file_id = message.photo[-1].file_id  # Берем фото наибольшего разрешения
            else:
                msg = bot.reply_to(message,
                                   "Для добавления ДЗ с фото, команда <code>/set предмет текст</code> должна быть в <b>подписи к фото</b>.",
                                   parse_mode="HTML")
                delete_message_with_delay(chat_id, message.message_id)
                delete_message_with_delay(msg.chat.id, msg.message_id)
                return
        elif message.content_type == 'text':
            command_text = message.text.strip()

        if not command_text.lower().startswith('/set '):
            return  # На всякий случай, чтобы этот хендлер обрабатывал только команды /set

        # Парсинг аргументов из command_text: /set "Предмет из нескольких слов" Задание тут
        # или /set Предмет Задание тут
        parts = command_text.split(' ', 1)  # Отделяем "/set " от остального
        if len(parts) < 2 or not parts[1].strip():
            msg = bot.reply_to(message,
                               "Неверный формат. Используйте: <code>/set \"предмет\" текст</code> или <code>/set предмет текст</code>. Или отправьте фото с такой подписью.",
                               parse_mode="HTML")
            delete_message_with_delay(chat_id, message.message_id)
            delete_message_with_delay(msg.chat.id, msg.message_id)
            return

        args_line = parts[1].strip()
        subject = ""
        value = ""

        if args_line.startswith('"'):  # Если предмет в кавычках
            end_quote_index = args_line.find('"', 1)
            if end_quote_index == -1:
                msg = bot.reply_to(message,
                                   "Ошибка в кавычках для предмета. Используйте: <code>/set \"предмет в кавычках\" текст задания</code>",
                                   parse_mode="HTML")
                delete_message_with_delay(chat_id, message.message_id)
                delete_message_with_delay(msg.chat.id, msg.message_id)
                return
            subject = args_line[1:end_quote_index].strip()
            value = args_line[end_quote_index + 1:].strip()
        else:  # Если предмет одно слово
            subject_parts = args_line.split(' ', 1)
            subject = subject_parts[0].strip()
            if len(subject_parts) > 1:
                value = subject_parts[1].strip()

        if not subject:
            msg = bot.reply_to(message, "Название предмета не может быть пустым.")
            delete_message_with_delay(chat_id, message.message_id)
            delete_message_with_delay(msg.chat.id, msg.message_id)
            return

        if not value and not photo_file_id:  # Если нет ни текста, ни фото, то ДЗ неполное
            msg = bot.reply_to(message, "Текст задания не может быть пустым, если не прикреплена фотография.")
            delete_message_with_delay(chat_id, message.message_id)
            delete_message_with_delay(msg.chat.id, msg.message_id)
            return

        # Вызываем вспомогательную функцию для сохранения и подтверждения
        _save_and_confirm_homework(message, subject, value, photo_file_id, user)

        delete_message_with_delay(chat_id, message.message_id)  # Удаляем команду /set или фото с командой

    except Exception as e:
        error_msg_text = f"Произошла ошибка при добавлении ДЗ: {str(e)}"
        print(error_msg_text)  # Логируем в консоль для отладки
        log_debug_info(
            f"Ошибка в /set от {user.id} (@{user.username}): {e}. Исходное сообщение: {message.text or message.caption}")
        msg = bot.reply_to(message, error_msg_text)
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)


@bot.message_handler(commands=['del'])
def delete_board(message: Message):
    """Обработчик команды /del для удаления ДЗ по предмету."""
    user = message.from_user
    chat_id = message.chat.id
    log_debug_info(f"Пользователь: {user.id} ( @{user.username} ) использовал команду: {message.text} в чате {chat_id}")

    if user.id in muted_users:
        msg = bot.reply_to(message, "Вы не можете использовать эту команду.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return

    if chat_id not in TELEGRAM_CHAT_ID and chat_id != DEBUG_CHAT_ID:
        msg = bot.reply_to(message, "Эта команда доступна только в настроенных рабочих чатах.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return

    # Парсинг аргументов: /del "Предмет из нескольких слов" или /del Предмет
    parts = message.text.split(' ', 1)  # Отделяем "/del " от остального
    if len(parts) < 2 or not parts[1].strip():
        msg = bot.reply_to(message,
                           "Неверный формат. Используйте: <code>/del \"предмет\"</code> или <code>/del предмет</code>",
                           parse_mode="HTML")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return

    key_to_delete_raw = parts[1].strip()

    # Удаляем кавычки, если они есть по краям
    if key_to_delete_raw.startswith('"') and key_to_delete_raw.endswith('"'):
        key_to_delete = key_to_delete_raw[1:-1].strip().lower()
    else:
        key_to_delete = key_to_delete_raw.lower()

    if not key_to_delete:
        msg = bot.reply_to(message, "Название предмета для удаления не может быть пустым.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return

    if key_to_delete in board_data:
        del board_data[key_to_delete]
        save_data()
        msg = bot.reply_to(message, f"✅ ДЗ по \"{key_to_delete.capitalize()}\" удалено.")
    else:
        msg = bot.reply_to(message, f"Предмет \"{key_to_delete.capitalize()}\" не найден.")

    delete_message_with_delay(chat_id, message.message_id)
    delete_message_with_delay(msg.chat.id, msg.message_id, delay=15)


@bot.message_handler(commands=['start'])
def send_welcome(message: Message):
    """Обработчик команд /start и /чтозадали, выводит главное меню."""
    user = message.from_user
    chat_id = message.chat.id
    log_debug_info(f"Пользователь: {user.id} ( @{user.username} ) использовал команду: {message.text} в чате {chat_id}")

    delete_message_with_delay(chat_id, message.message_id, delay=1)  # Удаляем команду пользователя

    try:
        msg = bot.send_message(
            chat_id,
            "Добро пожаловать! Выберите действия:",
            reply_markup=main_menu()
        )
        delete_message_with_delay(msg.chat.id, msg.message_id,
                                  delay=180)  # Меню исчезнет через 3 минуты, если не используется

    except Exception as e:
        print(f"Ошибка при отправке приветственного сообщения: {e}")
        log_debug_info(f"Ошибка при отправке приветственного сообщения для {user.id}: {e}")


@bot.message_handler(commands=['mute'])
def mute_user(message: Message):
    """Команда администратора для заглушения пользователя (запрет на использование /set и /del)."""
    user = message.from_user
    chat_id = message.chat.id
    log_debug_info(f"Попытка MUTE от {user.id} ({user.username}): {message.text}")

    delete_message_with_delay(chat_id, message.message_id)  # Удаляем команду

    if user.id not in ADMIN_ID:
        msg = bot.reply_to(message, "У вас нет прав для использования этой команды.")
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return
    try:
        args = message.text.split(' ', 1)
        if len(args) < 2:
            msg = bot.reply_to(message, "Неверный формат. Используйте: <code>/mute &lt;user_id&gt;</code>",
                               parse_mode="HTML")
            delete_message_with_delay(msg.chat.id, msg.message_id)
            return

        try:
            user_id_to_mute = int(args[1])
        except ValueError:
            msg = bot.reply_to(message, "Укажите корректный ID пользователя (число).")
            delete_message_with_delay(msg.chat.id, msg.message_id)
            return

        muted_users.add(user_id_to_mute)
        save_muted_users()
        msg = bot.reply_to(message,
                           f"🚫 Пользователь с ID <code>{user_id_to_mute}</code> теперь не может использовать команды /set и /del.",
                           parse_mode="HTML")
        log_debug_info(f"Администратор {user.id} заглушил пользователя {user_id_to_mute}")
        delete_message_with_delay(msg.chat.id, msg.message_id, delay=20)

    except Exception as e:
        msg = bot.reply_to(message, f"Ошибка при заглушении: {str(e)}")
        log_debug_info(f"Ошибка в /mute: {e}")
        delete_message_with_delay(msg.chat.id, msg.message_id)


@bot.message_handler(commands=['restart'])
def restart_bot(message: Message):
    """Команда администратора для перезапуска бота"""
    user = message.from_user
    chat_id = message.chat.id
    log_debug_info(f"Пользователь: {user.id} ( @{user.username} ) использовал команду: {message.text} в чате {chat_id}")

    if user.id not in ADMIN_ID:
        msg = bot.reply_to(message, "У вас нет прав для использования этой команды.")
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return

    try:
        msg = bot.reply_to(message, "Перезапуск бота...")
        log_debug_info(f"Администратор {user.id} инициировал перезапуск бота")
        time.sleep(1)
        delete_message_with_delay(chat_id, message.message_id)
        delete_message_with_delay(msg.chat.id, msg.message_id)

        # Перезапуск бота
        os.execl(sys.executable, sys.executable, *sys.argv)

    except Exception as e:
        error_msg = f"Ошибка при перезапуске: {str(e)}"
        bot.reply_to(message, error_msg)
        log_debug_info(f"Ошибка в /restart: {e}")


@bot.message_handler(commands=['unmute'])
def unmute_user(message: Message):
    """Команда администратора для разглушения пользователя."""
    user = message.from_user
    chat_id = message.chat.id
    log_debug_info(f"Попытка UNMUTE от {user.id} ({user.username}): {message.text}")

    delete_message_with_delay(chat_id, message.message_id)  # Удаляем команду

    if user.id not in ADMIN_ID:
        msg = bot.reply_to(message, "У вас нет прав для использования этой команды.")
        delete_message_with_delay(msg.chat.id, msg.message_id)
        return
    try:
        args = message.text.split(' ', 1)
        if len(args) < 2:
            msg = bot.reply_to(message, "Неверный формат. Используйте: <code>/unmute &lt;user_id&gt;</code>",
                               parse_mode="HTML")
            delete_message_with_delay(msg.chat.id, msg.message_id)
            return

        try:
            user_id_to_unmute = int(args[1])
        except ValueError:
            msg = bot.reply_to(message, "Укажите корректный ID пользователя (число).")
            delete_message_with_delay(msg.chat.id, msg.message_id)
            return

        muted_users.discard(user_id_to_unmute)  # Удаляем пользователя из набора заглушенных, если он там есть
        save_muted_users()
        msg = bot.reply_to(message,
                           f"✅ Пользователь с ID <code>{user_id_to_unmute}</code> теперь может использовать команды /set и /del.",
                           parse_mode="HTML")
        log_debug_info(f"Администратор {user.id} разглушил пользователя {user_id_to_unmute}")
        delete_message_with_delay(msg.chat.id, msg.message_id, delay=20)

    except Exception as e:
        msg = bot.reply_to(message, f"Ошибка при разглушении: {str(e)}")
        log_debug_info(f"Ошибка в /unmute: {e}")
        delete_message_with_delay(msg.chat.id, msg.message_id)


def console_command_handler():
    """Обработчик консольных команд для администратора."""
    while True:
        try:
            command = input().strip()

            if command.startswith('log '):
                # Извлекаем текст из команды log "текст"
                if command.startswith('log "'):
                    # Ищем закрывающую кавычку
                    end_quote_index = command.find('"', 5)
                    if end_quote_index == -1:
                        print("Ошибка: отсутствует закрывающая кавычка")
                        continue
                    text_to_log = command[5:end_quote_index]
                else:
                    # Без кавычек - берем все после 'log '
                    text_to_log = command[4:]

                if text_to_log:
                    try:
                        log_debug_info(f"[КОНСОЛЬ] {text_to_log}")
                        print(f"Текст отправлен в дебаг чат: {text_to_log}")
                    except Exception as e:
                        print(f"Ошибка при отправке в дебаг чат: {e}")
                else:
                    print("Текст для логирования не может быть пустым")

            elif command == 'exit':
                print("Завершение работы консольного обработчика")
                break

            elif command == 'help':
                print("Доступные команды:")
                print('  log "текст" - отправить текст в дебаг чат')
                print('  exit - завершить работу консольного обработчика')
                print('  help - показать эту справку')
                print('  restart - перезапустить бота')

            elif command == 'restart':
                print('Перезапуск из консоли...')
                log_debug_info("Перезапуск бота из консоли...")
                os.execl(sys.executable, sys.executable, *sys.argv)

            else:
                print(f"Неизвестная команда: {command}. Введите 'help' для справки.")

        except KeyboardInterrupt:
            print("\nЗавершение работы консольного обработчика")
            break
        except Exception as e:
            print(f"Ошибка в консольном обработчике: {e}")


# ==============================================
# СИСТЕМА ПОИСКА ПРЕДМЕТА ПО ТЕКСТУ
# ==============================================

def build_subject_alias_map():
    """
    Строит отображение алиас -> canonical_subject_key (нижний регистр).
    Алиасы включают:
      - полное ключевое имя (как в board_data)
      - первые 4 символа полного имени (если >=3 символа)
      - версия без пробелов (чтобы ловить 'изобразительное' и 'изобразит' варианты)
    """
    alias_map = {}
    for subj_key in board_data.keys():
        if not subj_key:
            continue
        key = subj_key.lower()
        alias_map[key] = key  # полное имя -> имя
        # первые 4 символа (если длина >= 3; используем min(4,len))
        short = key.replace(" ", "")[:4]  # убираем пробелы и берем первые 4 буквы
        if len(short) >= 2:
            alias_map[short] = key
        # версия без пробелов
        nospace = key.replace(" ", "")
        if nospace and nospace != key:
            alias_map[nospace] = key
        # также добавим первые 4 букв без пробелов отдельно на всякий случай
        if len(nospace) >= 2:
            alias_map[nospace[:4]] = key
    return alias_map


def normalize_word(w):
    """Нормализует слово для сравнения: удаляет небуквенные символы, приводит к нижнему регистру."""
    return re.sub(r'[^\wа-яё]', '', w.lower()).strip()


def find_subject_in_message(text: str):
    """
    Ищет упоминание предмета в сообщении.

    Args:
        text: Текст сообщения

    Returns:
        str or None: Ключ предмета в нижнем регистре или None, если не найден
    """
    if not text:
        return None

    text_norm = text.lower().strip()
    alias_map = build_subject_alias_map()

    # Паттерны для поиска
    patterns = [
        r'что\s+(задали|по)\s+(.+)',
        r'что\s+по\s+(.+)',
        r'что\s+задали\s+по\s+(.+)',
        r'какое\s+д[з]?\s+по\s+(.+)',
        r'д[з]?\s+по\s+(.+)',
        r'что\spo\s(.+)',  # на случай транслита 'po'
    ]

    for pattern in patterns:
        match = re.search(pattern, text_norm)
        if match:
            # берем последний захваченный блок (может содержать слова)
            subject_block = match.group(match.lastindex)
            if not subject_block:
                continue
            # убираем лишние слова/знаки на конце
            # возьмём первое слово либо всю фразу до знака препинания
            subject_block = subject_block.strip()
            # если в блоке есть пробелы - попробуем сначала слово, затем фразу
            # попробуем варианты: первое слово, полная фраза, фраза без пробелов
            candidates = []
            first_word = subject_block.split()[0]
            candidates.append(normalize_word(first_word))
            candidates.append(normalize_word(subject_block))
            candidates.append(normalize_word(subject_block.replace('"', '').replace("'", "")))
            candidates.append(subject_block.replace('"', '').replace("'", "").strip())
            for cand in candidates:
                if not cand:
                    continue
                if cand in alias_map:
                    return alias_map[cand]
                # также попробуем проверить первые 4 букв
                cand_short = cand.replace(" ", "")[:4]
                if cand_short in alias_map:
                    return alias_map[cand_short]

    # Если не нашли по паттернам, ищем прямое упоминание алиасов в тексте
    # Ищем более длинные алиасы первыми (чтобы избежать ложных коротких совпадений)

    return None


# Общий обработчик сообщений: ищет фразы типа "что задали по X" и отвечает
@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_general_queries(message: Message):
    user = message.from_user
    chat_id = message.chat.id

    # Не мешаем обработчикам состояний и командам
    if message.text is None:
        return

    text = message.text.strip()
    if not text:
        return

    # Пропускаем команды, они обрабатываются отдельно
    if text.startswith('/'):
        return

    # Если пользователь заглушен — игнорируем

    try:
        subject_key = find_subject_in_message(text)
        if not subject_key:
            return  # не распознали запрос предмета

        # Если предмет найден в board_data
        if subject_key in board_data:
            log_debug_info(
                f"Пользователь: {user.id} ( @{user.username} ) спросил про предмет в чате {chat_id}. Тип контента: {message.content_type}. Текст/Подпись: {message.text or message.caption}")

            homework = board_data[subject_key]
            text_hw = homework.get("text", "Нет текстового задания.")
            photo_id = homework.get("photo_id")
            full_text = f"ДЗ по \"{subject_key.capitalize()}\":\n{text_hw}"

            origin_chat_id = message.chat.id
            user_id = message.from_user.id

            # Если личные сообщения (чат с ботом) — отправим только личным сообщением
            if message.chat.type == "private" or origin_chat_id == user_id:
                if photo_id:
                    try:
                        bot.send_photo(user_id, photo_id, caption=full_text)
                    except Exception as e:
                        log_debug_info(
                            f"Ошибка отправки фото ДЗ для '{subject_key}' пользователю {user_id}: {e}. Отправляю текстом.")
                        bot.send_message(user_id, full_text + "\n\n(Не удалось загрузить фото)")
                else:
                    bot.send_message(user_id, full_text)
            else:
                # Group message: дублируем в ЛС и отправляем в чат (с удалением)
                # Сначала ЛС
                if photo_id:
                    try:
                        bot.send_photo(user_id, photo_id, caption=full_text)
                    except Exception as e:
                        log_debug_info(
                            f"Ошибка отправки фото ДЗ в ЛС для '{subject_key}' пользователю {user_id}: {e}. Отправляю текстом.")
                        bot.send_message(user_id, full_text + "\n\n(Не удалось загрузить фото)")
                else:
                    bot.send_message(user_id, full_text)

                # Отправка в чат (который запросил)
                if photo_id:
                    try:
                        sent = bot.send_photo(origin_chat_id, photo_id, caption=full_text)

                    except Exception as e:
                        log_debug_info(
                            f"Ошибка отправки фото ДЗ в чат '{origin_chat_id}' для '{subject_key}': {e}. Отправляю текстом.")
                        sent = bot.send_message(origin_chat_id, full_text + "\n\n(Не удалось загрузить фото)")
                else:
                    sent = bot.send_message(origin_chat_id, full_text)

                # Удаление через 5 минут
                delete_message_with_delay(origin_chat_id, sent.message_id, delay=300)

            # Попытка удалить сообщение пользователя (чтобы не засорять чат) — с задержкой и игнорированием ошибок
            delete_message_with_delay(message.chat.id, message.message_id, delay=1)


    except Exception as e:
        print(f"Ошибка в обработчике общих запросов: {e}")
        log_debug_info(f"Ошибка в обработчике общих запросов от {message.from_user.id}: {e}")


# ==============================================
# ЗАПУСК БОТА
# ==============================================

if __name__ == '__main__':
    print("Бот запускается...")
    log_debug_info("Бот запущен.")

    # Запускаем обработчик консольных команд в отдельном потоке
    console_thread = threading.Thread(target=console_command_handler, daemon=True)
    console_thread.start()

    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except telebot.apihelper.ApiException as e:  # Более специфичное исключение для API ошибок Telegram
            print(f"Ошибка API Telegram: {e}")
            log_debug_info(f"Ошибка API Telegram: {e}. Переподключение через 10 секунд...")
            time.sleep(10)
        except ConnectionError as e:  # Ошибки сети
            print(f"Ошибка соединения: {e}")
            log_debug_info(f"Ошибка соединения: {e}. Переподключение через 30 секунд...")
            time.sleep(30)
        except Exception as e:  # Любые другие непредвиденные ошибки
            print(f"Критическая ошибка при работе бота: {e}")
            log_debug_info(
                f"Критическая ошибка (не перехваченная ранее) в основном цикле polling: {e}. Перезапуск через 60 секунд...")
            time.sleep(60)