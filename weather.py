"""Kickoff wind/temp forecast for outdoor stadiums via Open-Meteo (free, no key)."""
import json, urllib.request, pandas as pd

# stadium_id -> (lat, lon). Indoor / retractable venues are skipped (no wind effect).
STADIUMS = {
    "BAL00": (39.278, -76.623), "BOS00": (42.091, -71.264), "BUF00": (42.774, -78.787),
    "CAR00": (35.226, -80.853), "CHI98": (41.862, -87.617), "CIN00": (39.095, -84.516),
    "CLE00": (41.506, -81.700), "DEN00": (39.744, -105.020), "GNB00": (44.501, -88.062),
    "JAX00": (30.324, -81.637), "KAN00": (39.049, -94.484), "MIA00": (25.958, -80.239),
    "NAS00": (36.166, -86.771), "NYC01": (40.814, -74.074), "PHI00": (39.901, -75.168),
    "PIT00": (40.447, -80.016), "SEA00": (47.595, -122.332), "SFO01": (37.403, -121.970),
    "TAM00": (27.976, -82.503), "WAS00": (38.908, -76.865),
    "LON00": (51.556, -0.280), "LON02": (51.604, -0.066), "MEX00": (19.303, -99.150),
}
INDOOR = {"dome", "closed"}


def kickoff_weather(games: pd.DataFrame) -> dict:
    """games: schedule rows (stadium_id, roof, gameday, gametime ET). Returns game_id -> dict(wind, temp, precip)."""
    out = {}
    for _, g in games.iterrows():
        if g.roof in INDOOR or g.stadium_id not in STADIUMS:
            continue
        lat, lon = STADIUMS[g.stadium_id]
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               "&hourly=wind_speed_10m,wind_gusts_10m,temperature_2m,precipitation_probability"
               "&wind_speed_unit=mph&temperature_unit=fahrenheit&timezone=America/New_York&forecast_days=10")
        try:
            h = json.load(urllib.request.urlopen(url, timeout=20))["hourly"]
        except Exception as e:
            print("weather fail", g.game_id, e); continue
        target = f"{g.gameday}T{str(g.gametime)[:2]}:00"
        if target not in h["time"]:
            continue
        i = h["time"].index(target)
        # average kickoff hour through +3h (game window)
        sl = slice(i, min(i + 4, len(h["time"])))
        avg = lambda k: round(sum(h[k][sl]) / len(h[k][sl]), 1)
        out[g.game_id] = dict(wind=avg("wind_speed_10m"), gust=max(h["wind_gusts_10m"][sl]),
                              temp=avg("temperature_2m"), precip=max(h["precipitation_probability"][sl]))
    return out
