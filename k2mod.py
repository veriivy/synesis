import json, os
from pathlib import Path
from openai import OpenAI

poa1 = json.loads(Path("PoA1.json").read_text())
poa2 = json.loads(Path("PoA2.json").read_text())

client = OpenAI(
    api_key=os.environ["IFM-v1_jff4X7kSnSwfHtGs211wguSWSFk0a332akBRJlFQ7tvPmw7WLky0wQOFO5DwCalI"],
    base_url=os.environ.get("http://localhost:30000/v1", "https://api.ifm.ai/v1"),
)

resp = client.chat.completions.create(
    model=os.environ.get("IFM/K2-Horizon-375B-A23B", "moonshotai/Kimi-K2-Instruct"),
    messages=[
        {
            "role": "system",
            "content": (
                "You are a moderator comparing two plans of action. "
                "Do not pick a winner unless asked. "
                "Return JSON only: {similarities: string[], differences: "
                "[{topic, poa1, poa2}]}"
            ),
        },
        {
            "role": "user",
            "content": (
                "PoA1:\n"
                + json.dumps(poa1, indent=2)
                + "\n\nPoA2:\n"
                + json.dumps(poa2, indent=2)
            ),
        },
    ],
)
print(resp.choices[0].message.content)