# Zone Trading Terminal — Complete System

Demand/Supply zone स्कैनर, multi-source live data, Dhan broker (data + आर्डर),
multi-source AI हाइपोथेसिस, mobile dashboard, FastAPI backend + WebSocket,
Render deployment — सब कुछ एक साथ।

## अब सिस्टम में क्या-क्या है

| फाइल | काम |
|---|---|
| `zone_core.py`, `zone_validation.py` | मूल zone इंजन (एक असली bug ठीक किया गया - नीचे देखें) |
| `risk_engine.py`, `trade_setup.py` | capital/risk% से quantity, न्यूनतम RR 1:3 hard floor |
| `nifty_context.py` | Nifty 50 पर विपरीत-ज़ोन conflict-check |
| `instruments.py` | आपकी 226 instruments लिस्ट, multi-source से जुड़ी |
| `config.py`, `data_pipeline.py` | multi-source orchestration + candle-close caching |
| `providers/` | price / OI / FII-DII / news / AI (Groq→OpenRouter→Gemini) / Dhan |
| `hypothesis_engine.py` | 2-line हिंदी hypothesis (AI + rule-based fallback) |
| `dhan_mapping.py`, `build_dhan_mapping.py` | NSE symbol → Dhan security_id मैपिंग बनाना/पढ़ना |
| `scheduler.py` | candle-close-driven scan (अब **असली Dhan candles fetch** करता है) |
| `settings_store.py` | Settings **restart-proof** (data/settings.json में persist) |
| `execution.py` | Paper / Manual / Algo — तीनों का trade execution + journal |
| `main.py` | FastAPI backend: सभी dashboard API + WebSocket push notifications |
| `frontend/index.html` | Mobile dashboard: Zones / Instruments / Settings + ट्रेड लेने का बटन |
| `render.yaml`, `Procfile` | Render deployment |

## इस राउंड में क्या नया जुड़ा और टेस्ट हुआ

1. **`_fetch_candles` अब असली है** — Dhan के historical/intraday endpoint से candles लाता है
   (`dhan_mapping.py` की मैपिंग से security_id ढूंढकर)। Weekly/Monthly, Dhan के daily डेटा को resample करके बनते हैं।
   ✅ ऑफलाइन टेस्ट: mock Dhan response (dict-of-arrays और list-of-dicts दोनों shape), malformed input,
   weekly/monthly resample, date-range logic — सब सही।
2. **`build_dhan_mapping.py`** — एक बार चलाकर Dhan के scrip-master CSV से हर NSE symbol का security_id
   निकालकर `data/dhan_security_ids.json` बनाता है। `dhan_mapping.py` उसे runtime पर पढ़ता है;
   फ़ाइल न हो तो भी crash नहीं होता, सिर्फ़ Dhan वाला रास्ता skip होता है।
   ✅ ऑफलाइन टेस्ट: फ़ाइल-न-होने की स्थिति में clean graceful behaviour।
3. **`settings_store.py`** — Settings अब restart-proof हैं (पहले सिर्फ़ memory में थीं, deploy होते ही reset हो जातीं)।
   ✅ ऑफलाइन टेस्ट: defaults, save→reload, पुरानी/अधूरी settings.json पर merge, corrupted file पर safe fallback।
4. **`execution.py`** — तीन modes:
   - **Paper**: हमेशा सिर्फ़ log होता है, कभी broker को नहीं छूता
   - **Manual**: सिर्फ़ journal में दर्ज होता है ("मैंने खुद ले लिया")
   - **Algo**: **3 सुरक्षा गेट** — (1) dashboard का mode असल में "algo" होना चाहिए, (2) हर कॉल पर अलग से
     `confirm=True` explicitly भेजना होगा, (3) Dhan configured होना चाहिए, (4) setup eligible होना चाहिए (RR≥3, qty≥1)
     — चारों में से कोई भी एक भी न मिले तो ऑर्डर नहीं जाता।
     ✅ ऑफलाइन टेस्ट: सभी 3 modes + सभी 4 rejection gates अलग-अलग सही से चले।
5. **WebSocket (`/ws/alerts`)** — proximal alerts अब 10 सेकंड में push होते हैं (15-सेकंड पोलिंग अब भी बैकअप के तौर पर चलती है,
   अगर किसी proxy के पीछे WebSocket ब्लॉक हो जाए तो भी dashboard काम करता रहे)।
6. **`main.py` को असल में execute करके टेस्ट किया** — चूंकि इस sandbox में network नहीं है और असली `fastapi`
   install नहीं हो सका, मैंने एक हल्का FastAPI-जैसा stub बनाकर `main.py` के सारे route functions
   (health, settings, execute, trades, zones, instruments) को **सीधे कॉल करके चलाया** — सिर्फ़ syntax-check नहीं,
   असली runtime व्यवहार वेरीफाई हुआ। सिर्फ़ असली HTTP/WebSocket लेयर (uvicorn) अनटेस्टेड है।

## टेस्ट की स्थिति (अपडेटेड)
| हिस्सा | स्थिति |
|---|---|
| सभी .py फाइलें | py_compile पास |
| सभी providers (price/OI/FII-DII/news/AI/Dhan) | ऑफलाइन no-crash टेस्ट पास |
| zone_core + zone_validation (tf= बग ठीक) | end-to-end टेस्ट पास |
| candle-close caching (सभी 13 TF, Monthly बग ठीक) | टेस्ट पास |
| trade_setup + risk_engine | eligible/reject दोनों केस पास |
| Nifty conflict-check | 3 केस पास |
| Dhan candle parser + resampler | mock responses पर पास |
| dhan_mapping (फ़ाइल-न-होने की स्थिति) | पास |
| settings_store persistence | पास |
| execution.py (paper/manual/algo + 4 gates) | पास |
| main.py के route functions | stub के ज़रिए **actually चलाकर** पास |
| असली FastAPI/uvicorn HTTP सर्वर | ⚠️ untested (network नहीं) |
| असली Dhan/NSE/Yahoo/Groq/OpenRouter/Gemini live calls | ⚠️ untested (network नहीं) |
| Frontend ब्राउज़र में | ⚠️ untested, पर JS syntax-valid (node --check पास) |

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env      # जो keys हों भरें, बाकी खाली छोड़ें

# अगर Dhan इस्तेमाल करना है, पहले mapping बनाएं:
python build_dhan_mapping.py     # data/dhan_security_ids.json बनाता है

uvicorn main:app --reload
# ब्राउज़र में http://localhost:8000 खोलें
```

## जो अभी भी आपको खुद करना होगा
1. **NSE index (Nifty/GiftNifty) के लिए Dhan mapping** — `build_dhan_mapping.py` अभी सिर्फ़
   `NSE_INSTRUMENTS` (equity) को मैप करता है। Nifty जैसे index की Dhan security_id अलग सेगमेंट (`IDX_I`) में है -
   `nifty_context`/`scheduler.refresh_nifty_zones` के लिए यह अलग से जोड़ें।
2. **एक बार लाइव टेस्ट ज़रूर करें** — `build_dhan_mapping.py` चलाकर देखें कि Dhan का CSV column-names अभी भी
   वही हैं जो कोड में लिखे हैं (`SEM_TRADING_SYMBOL` आदि) - ब्रोकर अक्सर बिना नोटिस बदल देते हैं।
3. Paper mode में कम से कम कुछ दिन/हफ़्ते चलाकर `data/trade_log.jsonl` देखें, तभी algo mode arm करें।

## Dhan से live order कैसे जाता है (सुरक्षा याद रखें)
Settings page पर mode "algo" करें → हर individual trade पर "ट्रेड लें" दबाने पर browser एक बार और
`confirm()` पूछता है → तभी बैकएंड को `confirm:true` भेजा जाता है → बैकएंड फिर से mode+confirm+Dhan-configured+eligibility
चारों चेक करता है → तभी `dhan_client.place_order(..., enable_live_orders=True)` जाता है। यह जानबूझकर
कई परतों में रखा गया है ताकि कोई एक गलती (browser bug, backend restart, गलत setting) अकेले किसी असली ऑर्डर को ट्रिगर न कर सके।
