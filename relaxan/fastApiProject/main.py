import logging
import asyncio
import subprocess
from difflib import SequenceMatcher
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel, Field
from typing import  Optional
import aiohttp
from langchain_openai import ChatOpenAI

from files.products import products

BITRIX24_WEBHOOK_URL = 'my_webhook_url'

app = FastAPI()

logging.basicConfig(level=logging.INFO)

# Настройка OpenAI
llm = ChatOpenAI(model="gpt-4", temperature=0, api_key="my_api_key")

# Хранение последнего упомянутого товара для каждого пользователя
user_last_product = {}


class GetProduct(BaseModel):
    name: str = Field('', description="which product the user has in mind, e.g. есть гольфы, колготки")
    color: str = Field('', description="what color the user has in mind, e.g. черный")
    size: str = Field('', description="what size the user has in mind, e.g. 4")
    compression_class: str = Field('',
                                   description="If the user specifies compression 1, write in a value of I. If the user specifies compression 2, write in value II. In other cases any other values. e.g. компрессия 22 - 27 мм")
    country: str = Field('', description="which country the user is referring to, e.g. строана производитель Чехия")
    manufacturer: str = Field('', description="which manufacturer the user has in mind, e.g. фирма Calze")
    price: str = Field('',
                       description="what price the user has in mind. Write in the meaning of the number only, e.g. цена 50.")
    greeting: str = Field('',
                          description="recognize the user's sentence as a greeting, e.g. здравствуйте/привет/добрый день.")
    contacts: str = Field('', description="The user is interested in contacts, e.g. позвонить.")
    thank: str = Field('', description="The user would like to thank, e.g. спасибо.")
    advice: str = Field('', description="User asks for advice, e.g. что посоветуете.")
    interest: str = Field('',
                          description="The user is interested in how the product can be purchased, e.g. способ купить, как приобрести, как сделать заказ. как оформить заявку")
    place: str = Field('',
                       description="the user is ready to buy or place an order for the product, e.g. готов купить, хорошо оставлю заявку, давайте оформим")
    fsl: str = Field('', description="User wrote his/her Surname First Name Second Name, e.g. Иванов Сергей Андреевич.")
    phone: str = Field('', description="The user wrote his phone number, e.g. +375257903263.")
    city: str = Field('', description="The user wrote his city, e.g. Минск.")
    cancel: str = Field('', description="User wants to cancel data collection, e.g. Отмена/Не сейчас.")


llm_with_tools = llm.bind_tools([GetProduct])


def is_similar_name(keyword, product_name):
    if keyword.lower() in product_name.lower():
        return True
    matcher = SequenceMatcher(None, keyword.lower(), product_name.lower())
    return matcher.ratio() > 0.5


def is_similar_color(keyword, product_color):
    if keyword.lower() == product_color.lower():
        return True
    matcher = SequenceMatcher(None, keyword.lower(), product_color.lower())
    return matcher.ratio() > 0.7


def is_similar_manufacturer(keyword, product_manufacturer):
    if keyword.lower() == product_manufacturer.lower():
        return True
    matcher = SequenceMatcher(None, keyword.lower(), product_manufacturer.lower())
    return matcher.ratio() > 0.5


def is_similar_country(keyword, product_country):
    if keyword.lower() == product_country.lower():
        return True
    matcher = SequenceMatcher(None, keyword.lower(), product_country.lower())
    return matcher.ratio() > 0.5


def is_similar_compression(keyword, compression_class):
    if keyword.lower() == compression_class.lower()[:2] or keyword.lower() == compression_class.lower()[3::]:
        return True
    matcher = SequenceMatcher(None, keyword.lower(), compression_class.lower())
    return matcher.ratio() > 0.8


def find_products_by_keywords(name=None, color=None, size=None, compression_class=None, country=None, manufacturer=None,
                              price=None):
    matches = []
    for product in products:
        if name and not is_similar_name(name, product["name"]):
            continue
        if color and not is_similar_color(color, product["color"]):
            continue
        if size and size != product["size"]:
            continue
        if compression_class and not is_similar_compression(compression_class, product["compression_class"]):
            continue
        if country and not is_similar_country(country, product["country"]):
            continue
        if manufacturer and not is_similar_manufacturer(manufacturer, product["manufacturer"]):
            continue
        if price:
            try:
                price_float = float(price)
                lower_bound = price_float - 3
                upper_bound = price_float + 3
                if not (lower_bound <= float(product["price"]) <= upper_bound):
                    continue
            except ValueError:
                logging.warning(f"Invalid price format: {price}")
                continue
        matches.append(product)
    return matches


def format_product_info(product):
    index = product['name'].find(",")
    if index != -1:
        name = product['name'][:index].rstrip()
    else:
        name = product['name']
    stock_info = "\n".join([f"Магазин \"{store}\": {quantity} единиц" for store, quantity in product['stock'].items()])
    return (
        f"Наименование: {name}\n"
        f"Цена: {product['price']} BYN\n"
        f"Размер: {product['size']}\n"
        f"Компрессия: {product['compression_class']}\n"
        f"Цвет: {product['color']}\n"
        f"Магазины, где можно приобрести:\n{stock_info}"
    )


async def send_to_bitrix24(lead_data):
    params = {
        'fields': {
            'TITLE': f"Заявка от клиента: {lead_data.get('last_name', '')} {lead_data.get('first_name', '')} {lead_data.get('middle_name', '')}",
            'NAME': lead_data.get('first_name', ''),
            'LAST_NAME': lead_data.get('last_name', ''),
            'SECOND_NAME': lead_data.get('middle_name', ''),
            'PHONE': [{'VALUE': lead_data.get('phone', ''), 'VALUE_TYPE': 'WORK'}],
            'CITY': lead_data.get('city', ''),
            'COMMENTS': f"ФИО: {lead_data.get('last_name', '')} {lead_data.get('first_name', '')} {lead_data.get('middle_name', '')}\nНазвание товара: {lead_data.get('product_name', '')}"
                        f"\nЦвет товара: {lead_data.get('product_color', '')}\nРазмер товара: {lead_data.get('product_size', '')}"
                        f"\nТелефон: {lead_data.get('phone', '')}\nГород: {lead_data.get('city', '')}"
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(BITRIX24_WEBHOOK_URL, json=params) as resp:
            if resp.status == 200:
                return {"message": "Ваша заявка успешно отправлена!"}
            else:
                return {"message": "Произошла ошибка при отправке заявки. Пожалуйста, попробуйте позже."}


@app.post("/query")
async def handle_query(user_text: str, background_tasks: BackgroundTasks, user_id: Optional[int] = None):
    ai_msg = llm_with_tools.invoke(user_text)
    tool_calls = ai_msg.tool_calls

    if user_id not in user_last_product:
        user_last_product[user_id] = {
            'name': '',
            'color': '',
            'size': '',
            'compression_class': '',
            'country': '',
            'manufacturer': '',
            'price': ''
        }

    last_product = user_last_product.get(user_id, {
        'name': '',
        'color': '',
        'size': '',
        'compression_class': '',
        'country': '',
        'manufacturer': '',
        'price': ''
    })

    if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
        args = tool_calls[0].get('args', {})
        name = args.get('name', last_product['name'])
        color = args.get('color', last_product['color'])
        size = args.get('size', last_product['size'])
        compression_class = args.get('compression_class', '')
        country = args.get('country', '')
        manufacturer = args.get('manufacturer', '')
        price = args.get('price', last_product['price'])
        greeting = args.get('greeting', '')
        contacts = args.get('contacts', '')
        thank = args.get('thank', '')
        advice = args.get('advice', '')
        interest = args.get('interest', '')
        place = args.get('place', '')
        fsl = args.get('fsl', '')
        phone = args.get('phone', '')
        city = args.get('city', '')
        cancel = args.get('cancel', '')

        if place != "":
            user_last_product[user_id] = {
                'name': name,
                'color': color,
                'size': size,
                'compression_class': compression_class,
                'country': country,
                'manufacturer': manufacturer,
                'price': price
            }
            return {"message": "Для оформления заказа, пожалуйста напишите: Ваши ФИО, телефон и город."}

        if interest != "":
            return {
                "message": "Чтобы приобрести товар, Вы можете оставить заявку на него. Для этого потребуются: ФИО, телефон и город проживания."}

        if advice != "" and name == "":
            return {
                "message": "Мы рады предложить Вам широкий ассортимент качественных, комфортных и практичных ортопедических изделий от ведущих мировых производителей. В нашем салоне также в наличии — корректирующее белье, обувь и стельки, корректоры осанки и многое другое. Все товары, которые можно приобрести в ортопедическом салоне, отличаются высокой надежностью, превосходно зарекомендовали себя в эксплуатации и пользуются неизменно высоким спросом среди покупателей."}

        if thank != "":
            return {"message": "Благодарим Вас за обращение! Напишите, если Вас интересуют еще какие-то вопросы."}

        if contacts != "":
            return {
                "message": "Вы можете позвонить нашим менеджерам по телефону +375 (29) 5629049. Также предоставляем наши адреса магазинов: Минск, пр-т Мира, 1, пом.1058 (вход со стороны двора), Минск, ул. Петра Мстиславца 2, Минск, ул.Притыцкого, 29, ТЦ Тивали пав. 355, 3 этаж (ст. м. Спортивная)."}

        if greeting != "":
            return {
                "message": "🙌Здравствуйте! Мы рады видеть вас в компании Relaxsan. Напишите, какой товар вас интересует."}

        if cancel != "":
            return {"message": "Отмена оформления заказа. Вы можете продолжить поиск товаров."}

        user_last_product[user_id] = {
            'name': name,
            'color': color,
            'size': size,
            'compression_class': compression_class,
            'country': country,
            'manufacturer': manufacturer,
            'price': price
        }
        product_data = user_last_product[user_id]

        matches = find_products_by_keywords(
            name=product_data['name'],
            color=product_data['color'],
            size=product_data['size'],
            compression_class=product_data['compression_class'],
            country=product_data['country'],
            manufacturer=product_data['manufacturer'],
            price=product_data['price']
        )

        if matches:
            response = f"Вот что мне удалось найти по Вашему запросу:\n\n" + "\n\n".join(
                [format_product_info(product) for product in matches[:3]])
            if len(matches) > 3:
                response += f"\n\nЯ нашел больше товаров, что Вы запросили. Пожалуйста, уточните детали, я покажу соответсвующие."
            return {"message": response}
        else:
            return {"message": "Товары не найдены. Пожалуйста, уточните ваш запрос."}

    return {"message": "Не удалось распознать запрос. Пожалуйста, напишите снова."}


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_parsing_script_periodically())


async def run_parsing_script_periodically():
    while True:
        try:
            subprocess.run(['python', 'pars.py'])
            logging.info("Pars.py script executed successfully.")
        except Exception as e:
            logging.error(f"Error running pars.py script: {e}")
        await asyncio.sleep(4000)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

