import sys, numpy as np, pandas as pd, model as M
if len(sys.argv)>1: M.REC_SLOPE=float(sys.argv[1])
p,s=M.load()
res=[]
for wk in range(4,19):
    act=p[(p.season==2025)&(p.week==wk)]
    active={}
    for t,g in act.groupby('posteam'):
        active[t]=set(g.rusher_player_id.dropna())|set(g.receiver_player_id.dropna())
    qbo={t:(g.passer_player_id.value_counts().index[0],'') for t,g in act[act.pass_attempt==1].groupby('posteam')}
    teams,pl,qbs,sh=M.build_slate(p,s,2025,wk,active=active,qb_override=qbo)
    sim=M.simulate(teams,pl,qbs,n=8000)
    out=M.summarize(teams,pl,qbs,sim,{})
    # actual
    sc=act[(act.touchdown==1)&(act.td_player_id.notna())]
    ytd=sc.groupby('td_player_id').size()
    first=sc.sort_values(['game_id','play_id']).groupby('game_id').first()
    firsttd=set(first.td_player_id)
    # first TD incl. non-offense: use all TD plays
    out['y_any']=out.pid.map(ytd).fillna(0)>0
    out['y_first']=out.pid.isin(firsttd)
    out['week']=wk
    res.append(out)
    
r=pd.concat(res); r.to_parquet(f'bt2025_{M.REC_SLOPE}.parquet')
