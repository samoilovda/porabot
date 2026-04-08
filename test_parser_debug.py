import asyncio
from bot.services.parser import InputParser

async def main():
    p = InputParser()
    print("Parsed 'in 10-10':")
    print(await p.parse('in 10-10', 'Europe/Moscow'))
    print("Parsed 'in 10':")
    print(await p.parse('in 10', 'Europe/Moscow'))
    print("Parsed 'сделать зарядку в 7 утра':")
    print(await p.parse('сделать зарядку в 7 утра', 'Europe/Moscow'))

asyncio.run(main())
