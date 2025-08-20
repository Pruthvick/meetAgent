from fastapi import FastAPI
from pydantic import BaseModel
from googleapiclient.discovery import build
from google.oauth2 import service_account
from datetime import datetime, timedelta
import openai, os, pytz, json
from langchain.chat_models import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate

app = FastAPI()

# 🔑 OpenAI Key
OPENAI_API_KEY="YOUR_API_KEY_HERE"
openai.api_key = OPENAI_API_KEY

# 🔑 Google Calendar credentials
SCOPES = ['https://www.googleapis.com/auth/calendar']
creds = service_account.Credentials.from_service_account_file(
    'credentials.json', scopes=SCOPES)
service = build('calendar', 'v3', credentials=creds)

# 📝 Request body
class Request(BaseModel):
    message: str

class ConfirmRequest(BaseModel):
    summary: str
    description: str
    start: str   # ISO format datetime string
    end: str     # ISO format datetime string
    attendees: list[str]

# 🔎 LangChain for parsing
llm = ChatOpenAI(model_name="gpt-3.5-turbo")
template = """
Extract meeting details from: {text}
Return JSON with keys: participants, date, start_time, duration_minutes.
"""
prompt = PromptTemplate.from_template(template)
chain = LLMChain(llm=llm, prompt=prompt)

def parse_text_with_langchain(text):
    resp = chain.predict(text=text).strip()
    try:
        return json.loads(resp)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{.*\}', resp, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"LLM returned non-JSON: {resp}")

# 📅 Free slot suggestion logic
def get_free_slots(service, start_dt, end_dt, slot_minutes=60, timezone='Asia/Kolkata'):
    body = {
        "timeMin": start_dt.isoformat() + 'Z',
        "timeMax": end_dt.isoformat() + 'Z',
        "items": [{"id": "primary"}]
    }
    result = service.freebusy().query(body=body).execute()
    busy_periods = result['calendars']['primary']['busy']

    tz = pytz.timezone(timezone)
    current = start_dt.astimezone(tz).replace(hour=10, minute=0, second=0, microsecond=0)
    suggestions = []

    while current < end_dt and len(suggestions) < 3:
        slot_end = current + timedelta(minutes=slot_minutes)

        if current.hour < 10 or slot_end.hour > 18:
            current = current + timedelta(days=1)
            current = current.replace(hour=10, minute=0)
            continue

        overlap = False
        for b in busy_periods:
            busy_start = datetime.fromisoformat(b['start'].replace('Z', '+00:00'))
            busy_end   = datetime.fromisoformat(b['end'].replace('Z', '+00:00'))
            if not (slot_end <= busy_start or current >= busy_end):
                overlap = True
                break

        if not overlap:
            local_start = current.astimezone(tz)
            local_end = slot_end.astimezone(tz)
            suggestions.append({
                "start": local_start.isoformat(),
                "end": local_end.isoformat(),
                "pretty": f"{local_start.strftime('%d %b, %-I:%M %p')} - {local_end.strftime('%-I:%M %p')}"
            })

        current += timedelta(minutes=30)

    return suggestions

# 🚀 Endpoint 1: Parse + suggest slots
@app.post("/schedule")
def schedule(req: Request):
    parsed_json = parse_text_with_langchain(req.message)
    duration = parsed_json['duration_minutes']

    start_date = datetime.utcnow()
    end_date   = start_date + timedelta(days=7)
    suggestions = get_free_slots(service, start_date, end_date, slot_minutes=duration)

    return {"meeting_details": parsed_json, "suggestions": suggestions}

# 🚀 Endpoint 2: Confirm + send invite
@app.post("/confirm")
def confirm(req: ConfirmRequest):
    attendees = [{"email": email} for email in req.attendees]

    event = {
        "summary": req.summary,
        "description": req.description,
        "start": {"dateTime": req.start, "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": req.end, "timeZone": "Asia/Kolkata"},
        "attendees": attendees,
    }

    event_result = service.events().insert(
        calendarId="primary",
        body=event,
        sendUpdates="all"
    ).execute()

    return {"status": "success", "event_link": event_result.get("htmlLink")}

@app.get("/")
def root():
    return {"msg": "Server is running!"}