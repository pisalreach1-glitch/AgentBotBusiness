# របៀបដាក់ដំណើរការ Bot លើ Render.com (Free 100% — ២៤/៧)

ឯកសារនេះណែនាំពីរបៀបយក **Telegram Business Agent Bot** ទៅដំណើរការលើ [Render.com](https://render.com) ដោយឥតគិតថ្លៃ (Free 100%) និងរត់ ២៤ ម៉ោងក្នុងមួយថ្ងៃ ទោះបីជាអ្នកបិទកុំព្យូទ័រក៏ដោយ។

---

## យន្តការដំណើរការ (Architecture)

1. **Web Service (Free Tier)**៖ Bot របស់យើងដំណើរការលើ Render Free Plan ដោយមាន **Health Check HTTP Server** (ស្ដាប់លើ Port `$PORT`) ឆ្លើយតប `200 OK` ដើម្បីឱ្យ Render ទទួលស្គាល់ថា Bot កំពុងដំណើរការយ៉ាងល្អ។
2. **UptimeRobot (Keep-Alive)**៖ ប្រើប្រាស់សេវាកម្ម [UptimeRobot.com](https://uptimerobot.com) (ឥតគិតថ្លៃ) ជួយ Ping ទៅកាន់ Web Service រៀងរាល់ 5 នាទីម្ដង ដើម្បីការពារកុំឱ្យ Render Free គេងលក់ (Sleep)។

---

## ជំហានទី ១ — Push កូដទៅកាន់ GitHub

កូដគម្រោងត្រូវបានរៀបចំឯកសារ `render.yaml` និង `Procfile` រួចរាល់។
សូមប្រាកដថាកូដចុងក្រោយត្រូវបាន Push ទៅកាន់ GitHub Repository របស់អ្នក៖
```bash
git add .
git commit -m "Add Render deployment support and health check server"
git push origin main
```

---

## ជំហានទី ២ — បង្កើត Web Service លើ Render.com

### វិធីងាយស្រួលបំផុត (តាមរយៈ Blueprint `render.yaml`)៖
1. ចូលទៅកាន់ [dashboard.render.com](https://dashboard.render.com/) ហើយចុះឈ្មោះ/Login ជាមួយគណនី GitHub។
2. ចុចប៊ូតុង **"New +"** (នៅខាងស្ដាំខាងលើ) -> រើសយក **"Blueprint"**។
3. ជ្រើសរើស Repository របស់អ្នក (`AgentBotBusiness` ឬ `pisalreach1-glitch/AgentBotBusiness`)។
4. Render នឹងអានឯកសារ `render.yaml` ដោយស្វ័យប្រវត្តិ។

---

### ឬបង្កើតដោយផ្ទាល់ (Manual Web Service)៖
1. ចុច **"New +"** -> **"Web Service"**
2. ជ្រើសរើស Repository `AgentBotBusiness` រួចចុច **Connect**
3. បំពេញព័ត៌មានដូចខាងក្រោម៖
   * **Name**: `business-agent-bot`
   * **Region**: `Singapore` (ជិតកម្ពុជាបំផុត)
   * **Branch**: `main`
   * **Runtime**: `Python 3`
   * **Build Command**: `pip install -r requirements.txt`
   * **Start Command**: `python -m app.main`
   * **Instance Type**: ជ្រើសរើស **Free**

---

## ជំហានទី ៣ — បំពេញ Environment Variables

នៅក្នុងផ្ទាំង **Environment Variables** លើ Render សូមចុច **"Add Environment Variable"** ហើយបំពេញតម្លៃដូចខាងក្រោម៖

| Key | Value (តម្លៃ) | ការពន្យល់ |
|---|---|---|
| `TELEGRAM_TOKEN` | *Token របស់ Bot របស់អ្នក* | យកពី @BotFather លើ Telegram |
| `GEMINI_API_KEY` | *API Key របស់អ្នក* | យកពី Google AI Studio |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model ឆ្លាតវៃ និងលឿនរបស់ Gemini |
| `DATABASE_PATH` | `data/business.sqlite3` | ទីតាំងរក្សាទុកទិន្នន័យ |
| `TIMEZONE` | `Asia/Phnom_Penh` | ម៉ោងតំបន់កម្ពុជា |
| `DAILY_AI_REQUEST_LIMIT` | `30` | ចំនួនសំណើ AI អតិបរមាក្នុង ១ ថ្ងៃ |
| `AI_MAX_OUTPUT_TOKENS` | `5000` | ប្រវែងអត្ថបទឆ្លើយតប |
| `ALLOWED_USER_IDS` | *(ទុកទទេ ឬដាក់ ID របស់អ្នក)* | បើទុកទទេ នរណាក៏អាច Chat បាន |

បន្ទាប់មកចុច **"Apply Changes"** ឬ **"Create Web Service"**។ Render នឹងចាប់ផ្ដើម Build និង Run Bot របស់អ្នក។ ពិនិត្យមើលផ្ទាំង Logs រហូតឃើញ៖
```text
Health check server running on port 10000
Application started
```

---

## ជំហានទី ៤ — កំណត់ UptimeRobot ដើម្បីឱ្យ Bot រត់ ២៤/៧ (Keep-Alive)

Render Free Plan នឹងផ្អាកដំណើរការ (Sleep) ប្រសិនបើគ្មានអ្នកចូលមើល Web លើសពី 15 នាទី។ ដើម្បីការពារកុំឱ្យ Sleep និងឱ្យ Bot ដើរ ២៤/៧៖

1. ចម្លងយក **URL របស់ Web Service** ពីលើគេក្នុង Render Dashboard (ឧទាហរណ៍៖ `https://business-agent-bot.onrender.com`)។
2. ចូលទៅកាន់ [UptimeRobot.com](https://uptimerobot.com) ហើយចុះឈ្មោះគណនី Free។
3. ចុច **"Add New Monitor"**៖
   * **Monitor Type**: ជ្រើសរើស `HTTP(s)`
   * **Friendly Name**: `Business Agent Bot Keep-Alive`
   * **URL (or IP)**: បិទភ្ជាប់ URL របស់ Render ខាងលើ (`https://business-agent-bot.onrender.com`)
   * **Monitoring Interval**: ជ្រើសរើស **Every 5 minutes**
4. ចុច **"Create Monitor"**។

🎉 **រួចរាល់ជាស្ថាពរ!** ចាប់ពីពេលនេះទៅ UptimeRobot នឹង Ping ទៅកាន់ Bot របស់អ្នករៀងរាល់ 5 នាទីម្ដង ធ្វើឱ្យ Bot នៅដំណើរការរហូត ២៤/៧ ទោះបីជាអ្នកបិទកុំព្យូទ័រក៏ដោយ!
