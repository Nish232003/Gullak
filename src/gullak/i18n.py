from __future__ import annotations

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "हिन्दी (Hindi)",
    "mr": "मराठी (Marathi)",
    "gu": "ગુજરાતી (Gujarati)",
    "ta": "தமிழ் (Tamil)",
    "te": "తెలుగు (Telugu)",
}

ADVICE_TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "below_cost": "{name}: Selling price (₹{price:.2f}) is below cost (₹{cost:.2f}).",
        "thin_margin": "{name}: Margin is only {margin_pct:.1f}% (cost ₹{cost:.2f}, price ₹{price:.2f}).",
        "close_to_expiry": "{name}: Stock expires in {days_to_expiry} day(s). Clear existing stock, do not restock.",
        "no_recent_sale": "{name}: No sale in the last {days_since_last_sale} days. Do not restock.",
        "overstock": "{name}: Stock covers {days_of_cover:.0f} days (>= {overstock_days:.0f} days). Do not restock.",
        "low_stock": "{name}: {stock:g} {unit} left (covers {days_of_cover:.1f} days, need {target_days:g} days). Restock {suggested_packs} pack(s).",
    },
    "hi": {
        "below_cost": "{name}: बिक्री मूल्य (₹{price:.2f}) लागत (₹{cost:.2f}) से कम है! दाम बढ़ाएं।",
        "thin_margin": "{name}: मार्जिन केवल {margin_pct:.1f}% है (लागत ₹{cost:.2f}, बिक्री ₹{price:.2f})।",
        "close_to_expiry": "{name}: स्टॉक {days_to_expiry} दिन में एक्सपायर होगा। अभी नया माल न मंगाएं।",
        "no_recent_sale": "{name}: पिछले {days_since_last_sale} दिनों से कोई बिक्री नहीं। नया माल न मंगाएं।",
        "overstock": "{name}: {days_of_cover:.0f} दिन का स्टॉक बाकी है। अभी और न मंगाएं।",
        "low_stock": "{name}: केवल {stock:g} {unit} बचा है ({days_of_cover:.1f} दिन के लिए)। {suggested_packs} {pack} मंगाएं।",
    },
    "mr": {
        "below_cost": "{name}: विक्री किंमत (₹{price:.2f}) खरेदी किमतीपेक्षा (₹{cost:.2f}) कमी आहे! दर वाढवा.",
        "thin_margin": "{name}: नफा फक्त {margin_pct:.1f}% आहे (खरेदी ₹{cost:.2f}, विक्री ₹{price:.2f}).",
        "close_to_expiry": "{name}: साठा {days_to_expiry} दिवसांत एक्स्पायर होईल. नवीन माल मागवू नका.",
        "no_recent_sale": "{name}: गेल्या {days_since_last_sale} दिवसांत विक्री नाही. नवीन माल मागवू नका.",
        "overstock": "{name}: {days_of_cover:.0f} दिवसांचा साठा शिल्लक आहे. सध्या मागवू नका.",
        "low_stock": "{name}: फक्त {stock:g} {unit} शिल्लक आहे ({days_of_cover:.1f} दिवसांसाठी). {suggested_packs} {pack} मागवा.",
    },
    "gu": {
        "below_cost": "{name}: વેચાણ કિંમત (₹{price:.2f}) ખરીદ કિંમત (₹{cost:.2f}) કરતાં ઓછી છે! ભાવ વધારો.",
        "thin_margin": "{name}: નફો માત્ર {margin_pct:.1f}% છે (ખરીદ ₹{cost:.2f}, વેચાણ ₹{price:.2f}).",
        "close_to_expiry": "{name}: સ્ટોક {days_to_expiry} દિવસમાં એક્સપાયર થશે. નવો માલ ન મંગાવો.",
        "no_recent_sale": "{name}: છેલ્લા {days_since_last_sale} દિવસમાં કોઈ વેચાણ નથી. નવો માલ ન મંગાવો.",
        "overstock": "{name}: {days_of_cover:.0f} દિવસનો સ્ટોક બાકી છે. અત્યારે ન મંગાવો.",
        "low_stock": "{name}: માત્ર {stock:g} {unit} બાકી છે ({days_of_cover:.1f} દિવસ માટે). {suggested_packs} {pack} મંગાવો.",
    },
    "ta": {
        "below_cost": "{name}: விற்பனை விலை (₹{price:.2f}) வாங்கிய விலையை (₹{cost:.2f}) விட குறைவு! விலையை உயர்த்தவும்.",
        "thin_margin": "{name}: லாபம் {margin_pct:.1f}% மட்டுமே (வாங்கியது ₹{cost:.2f}, விற்பனை ₹{price:.2f}).",
        "close_to_expiry": "{name}: சரக்கு {days_to_expiry} நாட்களில் காலாவதியாகும். புதிய சரக்கு வேண்டாம்.",
        "no_recent_sale": "{name}: கடந்த {days_since_last_sale} நாட்களில் விற்பனை இல்லை. புதிய சரக்கு வேண்டாம்.",
        "overstock": "{name}: {days_of_cover:.0f} நாட்களுக்கான சரக்கு உள்ளது. இப்போது வாங்க வேண்டாம்.",
        "low_stock": "{name}: {stock:g} {unit} மட்டுமே உள்ளது ({days_of_cover:.1f} நாட்களுக்கு). {suggested_packs} பேக் வாங்கவும்.",
    },
    "te": {
        "below_cost": "{name}: అమ్మకపు ధర (₹{price:.2f}) కొనుగోలు ధర (₹{cost:.2f}) కంటే తక్కువగా ఉంది! ధర పెంచండి.",
        "thin_margin": "{name}: లాభం కేవలం {margin_pct:.1f}% మాత్రమే (కొనుగోలు ₹{cost:.2f}, అమ్మకం ₹{price:.2f}).",
        "close_to_expiry": "{name}: స్టాక్ {days_to_expiry} రోజుల్లో గడువు ముగుస్తుంది. కొత్త సరుకు తెప్పించవద్దు.",
        "no_recent_sale": "{name}: గత {days_since_last_sale} రోజుల్లో అమ్మకం లేదు. కొత్త సరుకు తెప్పించవద్దు.",
        "overstock": "{name}: {days_of_cover:.0f} రోజులకు సరిపడా స్టాక్ ఉంది. ఇప్పుడు తెప్పించవద్దు.",
        "low_stock": "{name}: కేవలం {stock:g} {unit} మాత్రమే మిగిలింది ({days_of_cover:.1f} రోజులకు). {suggested_packs} ప్యాక్‌లు తెప్పించండి.",
    },
}


def format_advice_message(lang: str, reason: str, **kwargs: object) -> str:
    """Format an advice message in the requested language, falling back to English."""
    lang_code = lang if lang in ADVICE_TEMPLATES else "en"
    template = ADVICE_TEMPLATES[lang_code].get(reason) or ADVICE_TEMPLATES["en"][reason]
    return template.format(**kwargs)
