"""Red-zone / end-zone usage by player, built from nflverse play-by-play (current + prior season)."""
import numpy as np, pandas as pd


def _season(p, season):
    d = p[(p.season == season) & (p.season_type == "REG")]
    ru = d[(d.rush_attempt == 1) & d.rusher_player_id.notna()]
    pa = d[(d.pass_attempt == 1) & (d.sack == 0) & d.receiver_player_id.notna()]
    ez = pa.air_yards.fillna(-99) >= pa.yardline_100
    team_rz_ru = ru[ru.yardline_100 <= 20].groupby("posteam").size()
    team_i10 = ru[ru.yardline_100 <= 10].groupby("posteam").size()
    team_rz_tg = pa[pa.yardline_100 <= 20].groupby("posteam").size()
    team_ez = pa[ez].groupby("posteam").size()
    r = ru.groupby(["rusher_player_id", "rusher_player_name", "posteam"]).agg(
        rz_car=("yardline_100", lambda y: (y <= 20).sum()), i10_car=("yardline_100", lambda y: (y <= 10).sum()),
        i5_car=("yardline_100", lambda y: (y <= 5).sum()), rush_td=("rush_touchdown", "sum"),
        g_r=("game_id", "nunique")).reset_index().rename(columns={"rusher_player_id": "pid", "rusher_player_name": "name"})
    pa = pa.assign(ez=ez.astype(int), rz=(pa.yardline_100 <= 20).astype(int))
    c = pa.groupby(["receiver_player_id", "receiver_player_name", "posteam"]).agg(
        tgt=("ez", "size"), rz_tgt=("rz", "sum"), ez_tgt=("ez", "sum"), rec_td=("pass_touchdown", "sum"),
        g_c=("game_id", "nunique")).reset_index().rename(columns={"receiver_player_id": "pid", "receiver_player_name": "name"})
    m = r.merge(c, on=["pid", "name", "posteam"], how="outer").fillna(0)
    m["g"] = m[["g_r", "g_c"]].max(axis=1).astype(int)
    m["rz_car_sh"] = m.rz_car / m.posteam.map(team_rz_ru)
    m["i10_car_sh"] = m.i10_car / m.posteam.map(team_i10)
    m["rz_tgt_sh"] = m.rz_tgt / m.posteam.map(team_rz_tg)
    m["ez_tgt_sh"] = m.ez_tgt / m.posteam.map(team_ez)
    m["rz_opp"] = m.rz_car + m.rz_tgt
    m["td"] = m.rush_td + m.rec_td
    m["season"] = season
    m = m[m.rz_opp >= 2].drop(columns=["g_r", "g_c"])
    return m


def table(p, season):
    out = pd.concat([_season(p, season), _season(p, season - 1)])
    out = out.rename(columns={"posteam": "team"}).replace([np.inf, -np.inf], np.nan).fillna(0).round(3)
    for c in ["rz_car", "i10_car", "i5_car", "rush_td", "tgt", "rz_tgt", "ez_tgt", "rec_td", "rz_opp", "td"]:
        out[c] = out[c].astype(int)
    return out.sort_values(["season", "rz_opp"], ascending=[False, False]).to_dict("records")
