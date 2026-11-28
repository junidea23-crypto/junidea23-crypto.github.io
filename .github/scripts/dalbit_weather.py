"""달빛 사주관 아침 알림용 날씨 파일 만들기.

기상청 단기예보(getVilageFcst)와 에어코리아 대기질 예보통보(getMinuDustFrcstDspth)를
시/도별로 받아 saju/weather.json 으로 저장해요. 앱은 이 파일만 읽어서 API 키가 앱에 들어가지 않아요.
필요한 비밀값: DATA_GO_KR_KEY (공공데이터포털 '일반 인증키(Decoding)')
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
OUT = os.environ.get("DALBIT_WX_OUT", "saju/weather.json")
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.now(KST)

# 시/도 대표 지점의 기상청 격자(nx, ny)와 에어코리아 예보 권역 이름
REGIONS = {
    "서울": (60, 127, ["서울"]),
    "부산": (98, 76, ["부산"]),
    "대구": (89, 90, ["대구"]),
    "인천": (55, 124, ["인천"]),
    "광주": (58, 74, ["광주"]),
    "대전": (67, 100, ["대전"]),
    "울산": (102, 84, ["울산"]),
    "세종": (66, 103, ["세종"]),
    "경기": (60, 121, ["경기남부", "경기북부"]),
    "강원": (73, 134, ["영서", "영동"]),
    "충북": (69, 106, ["충북"]),
    "충남": (68, 100, ["충남"]),
    "전북": (63, 89, ["전북"]),
    "전남": (51, 67, ["전남"]),
    "경북": (91, 106, ["경북"]),
    "경남": (91, 77, ["경남"]),
    "제주": (52, 38, ["제주"]),
}
GRADE_ORDER = ["좋음", "보통", "나쁨", "매우나쁨"]
VILAGE = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
DUST = "http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMinuDustFrcstDspth"


def get_json(url, params, tries=3):
    q = urllib.parse.urlencode(params)
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=30) as r:
                txt = r.read().decode("utf-8")
            return json.loads(txt)
        except Exception as e:  # 기상청 서버가 가끔 XML 오류나 빈 응답을 줘요
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"요청 실패: {url} ({last})")


def latest_base(now):
    """가장 최근 발표(02,05,08,11,14,17,20,23시, 발표 후 15분 지난 것)."""
    t = now - dt.timedelta(minutes=15)
    hours = [h for h in (2, 5, 8, 11, 14, 17, 20, 23) if h <= t.hour]
    if not hours:
        t -= dt.timedelta(days=1)
        hours = [23]
    return t.strftime("%Y%m%d"), f"{hours[-1]:02d}00"


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize(items):
    """시간별 예보 → 날짜별 요약."""
    days = {}
    for it in items:
        d = it["fcstDate"]
        h = int(it["fcstTime"][:2])
        days.setdefault(d, {}).setdefault(h, {})[it["category"]] = it["fcstValue"]
    out = {}
    for d, hours in days.items():
        key = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        tmps = [num(v.get("TMP")) for v in hours.values() if num(v.get("TMP")) is not None]
        tmn = next((num(v["TMN"]) for v in hours.values() if "TMN" in v), None)
        tmx = next((num(v["TMX"]) for v in hours.values() if "TMX" in v), None)
        if tmn is None and tmps and min(hours) <= 6:
            tmn = min(tmps)
        if tmx is None and tmps and max(hours) >= 15:
            tmx = max(tmps)
        day = {h: v for h, v in hours.items() if 6 <= h <= 21}
        pops = [num(v.get("POP")) for v in day.values() if num(v.get("POP")) is not None]
        rain_hour, pty = None, 0
        for h in sorted(day):
            p = int(num(day[h].get("PTY")) or 0)
            if p:
                rain_hour, pty = h, p
                break
        skies = [int(num(v["SKY"])) for h, v in day.items() if "SKY" in v and 7 <= h <= 18]
        sky = max(set(skies), key=skies.count) if skies else None
        rec = {
            "tmn": round(tmn) if tmn is not None else None,
            "tmx": round(tmx) if tmx is not None else None,
            "pop": round(max(pops)) if pops else None,
            "sky": sky,
            "pty": pty,
            "rainHour": rain_hour,
        }
        # 하루 중 일부만 있는 날(예: 발표 직후 오늘 아침이 빠진 경우)은 기온이 비어 있을 수 있어요
        out[key] = rec
    return out


def worse(a, b):
    if not a:
        return b
    if not b:
        return a
    return a if GRADE_ORDER.index(a) >= GRADE_ORDER.index(b) else b


def dust_grades():
    """에어코리아 미세먼지(PM10)·초미세먼지(PM2.5) 예보 → {날짜: {권역: 등급}} (둘 중 나쁜 쪽)."""
    res = {}
    for code in ("PM10", "PM25"):
        for day in (NOW.date(), NOW.date() - dt.timedelta(days=1)):
            try:
                j = get_json(DUST, {"serviceKey": KEY, "returnType": "json", "numOfRows": 100, "pageNo": 1,
                                    "searchDate": day.isoformat(), "InformCode": code})
            except Exception as e:
                print("미세먼지 예보 실패:", code, day, e, file=sys.stderr)
                continue
            items = (((j.get("response") or {}).get("body") or {}).get("items")) or []
            # 같은 날짜 예보가 여러 번 발표되면 가장 최근 발표를 써요
            latest = {}
            for it in items:
                k = it.get("informData")
                if k and (k not in latest or it.get("dataTime", "") > latest[k].get("dataTime", "")):
                    latest[k] = it
            for k, it in latest.items():
                for part in (it.get("informGrade") or "").split(","):
                    if ":" not in part:
                        continue
                    name, g = [x.strip() for x in part.split(":", 1)]
                    if g in GRADE_ORDER:
                        cur = res.setdefault(k, {})
                        cur[name] = worse(cur.get(name), g)
            if latest:
                break
    return res


def main():
    if not KEY:
        sys.exit("DATA_GO_KR_KEY 비밀값이 없어요. GitHub 저장소 Settings → Secrets and variables → Actions 에 넣어 주세요.")
    try:
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    regions = data.get("regions") or {}
    bd, bt = latest_base(NOW)
    ok = 0
    for name, (nx, ny, _) in REGIONS.items():
        try:
            j = get_json(VILAGE, {"serviceKey": KEY, "pageNo": 1, "numOfRows": 1500, "dataType": "JSON",
                                  "base_date": bd, "base_time": bt, "nx": nx, "ny": ny})
            head = (j.get("response") or {}).get("header") or {}
            if head.get("resultCode") != "00":
                raise RuntimeError(head)
            items = j["response"]["body"]["items"]["item"]
            new = summarize(items)
            cur = regions.setdefault(name, {})
            for k, rec in new.items():
                old = cur.get(k, {})
                # 새 발표에 값이 비어 있으면(예: 오늘 최저기온) 예전 값을 그대로 둠요
                cur[k] = {f: (rec.get(f) if rec.get(f) is not None else old.get(f)) for f in set(rec) | set(old)}
            ok += 1
        except Exception as e:
            print("단기예보 실패:", name, e, file=sys.stderr)
        time.sleep(0.3)
    dust = dust_grades()
    for name, (_, _, areas) in REGIONS.items():
        for day, grades in dust.items():
            g = None
            for a in areas:
                g = worse(g, grades.get(a))
            if g and day in regions.get(name, {}):
                regions[name][day]["dust"] = g
    # 이틀 전보다 오래된 날짜는 지워요
    keep = (NOW.date() - dt.timedelta(days=2)).isoformat()
    for name in regions:
        regions[name] = {k: v for k, v in sorted(regions[name].items()) if k >= keep}
    if ok == 0 and not regions:
        sys.exit("모든 지역 예보를 받지 못했어요. 인증키와 활용신청 승인 상태를 확인해 주세요.")
    out = {
        "updated": NOW.isoformat(timespec="minutes"),
        "base": f"{bd} {bt}",
        "source": "날씨: 기상청 단기예보 · 미세먼지: 에어코리아(한국환경공단)",
        "regions": regions,
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"완료: {ok}/{len(REGIONS)}개 지역, 발표 {bd} {bt}")


if __name__ == "__main__":
    main()
