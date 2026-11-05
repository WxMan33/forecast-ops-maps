"""Self-test for the HRRR packager (synthetic inputs only; no network). Run: python pipeline/test_hrrr.py"""
import datetime as dt, math, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import hrrr  # noqa: E402

n = 0
def ok(c, m):
    global n
    assert c, m; n += 1

U = hrrr.UTC
now = dt.datetime(2026, 11, 4, 19, 30, tzinfo=U)
full = lambda h: set(range(0, h + 1))
# retention: newest usable run + the two newest complete 48-h runs; incomplete runs wait
av = {dt.datetime(2026, 11, 4, 18, tzinfo=U): set(range(0, 31)), dt.datetime(2026, 11, 4, 17, tzinfo=U): full(18),
      dt.datetime(2026, 11, 4, 16, tzinfo=U): full(18), dt.datetime(2026, 11, 4, 12, tzinfo=U): full(48),
      dt.datetime(2026, 11, 4, 6, tzinfo=U): full(48), dt.datetime(2026, 11, 4, 0, tzinfo=U): full(48)}
p = hrrr.plan(av, now)
ok([(c.hour, r, h) for c, r, h in p] == [(17, "latest", 18), (12, "extended", 48), (6, "extended", 48)], f"retention plan {p}")
# an extended run that is complete and newest is both latest and extended
p2 = hrrr.plan({dt.datetime(2026, 11, 4, 18, tzinfo=U): full(48), dt.datetime(2026, 11, 4, 12, tzinfo=U): full(48)}, now)
ok([(c.hour, r) for c, r, _ in p2] == [(18, "latest+extended"), (12, "extended")], f"extended newest {p2}")
# a run with a permanently missing hour is published (missing marked) only after the give-up delay
gap = {dt.datetime(2026, 11, 4, 13, tzinfo=U): full(18) - {7}}
ok(hrrr.plan(gap, dt.datetime(2026, 11, 4, 16, tzinfo=U)) == [], "a recent incomplete run waits")
ok(len(hrrr.plan(gap, dt.datetime(2026, 11, 4, 21, tzinfo=U))) == 1, "an old incomplete run is published with its missing hour")
ok(hrrr.plan({}, now) == [], "nothing available: nothing planned")

# classification from GRIB metadata only
base = {"typeOfLevel": "surface", "level": 0, "units": "kg m**-2", "stepUnits": 1}
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 5, "endStep": 6}, 6) == "apcp_hour", "(N-1)-N accumulation is the hourly field")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 0, "endStep": 6}, 6) == "apcp_run", "0-N accumulation is the run total")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 0, "endStep": 1}, 1) == "apcp_run+hour", "0-1 h is both")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 3, "endStep": 6}, 6) is None, "3-h accumulations are not mislabelled")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 5, "endStep": 6, "units": "m"}, 6) is None, "unexpected units are rejected")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 4, "endStep": 5}, 6) is None, "an accumulation for another hour is rejected")
ok(hrrr.classify({"shortName": "2t", "typeOfLevel": "heightAboveGround", "level": 2, "stepType": "instant", "units": "K"}, 3) == "t2m", "2-m temperature")
ok(hrrr.classify({"shortName": "2t", "typeOfLevel": "heightAboveGround", "level": 2, "stepType": "max", "units": "K"}, 3) is None, "a max temperature is not the instantaneous field")
ok(hrrr.classify({"shortName": "refc", "stepType": "instant"}, 3) == "refc", "composite reflectivity")
ok(hrrr.classify({"shortName": "refd", "typeOfLevel": "heightAboveGround", "level": 1000, "stepType": "instant"}, 3) == "refd1km", "1 km AGL reflectivity")
ok(hrrr.classify({"shortName": "refd", "typeOfLevel": "heightAboveGround", "level": 4000, "stepType": "instant"}, 3) is None, "4 km reflectivity is not mistaken for 1 km")
ok(hrrr.classify({"shortName": "refd", "typeOfLevel": "isothermal", "level": 263, "stepType": "max"}, 3) is None, "-10 C hourly max reflectivity is not mistaken for 1 km")

# encoding: missing stays missing; units converted
e = hrrr.encode("t2m", np.array([[273.15, np.nan]]))
ok(e[0, 0] == 320 and e[0, 1] == hrrr.MISS16, "0 C = 32.0 F packed as 320; NaN -> sentinel")
e = hrrr.encode("apcp_hour", np.array([[0.0, 1.234, np.nan, -0.5]]))
ok(list(e[0]) == [0, 123, hrrr.MISSU16, 0], "precip packed in 0.01 mm; NaN -> sentinel; never negative")

# projection: HRRR's first grid point maps to cell (0, 0); a 3 km step moves one cell
proj = {"Latin1InDegrees": 38.5, "Latin2InDegrees": 38.5, "LaDInDegrees": 38.5, "LoVInDegrees": 262.5, "radius": 6371229,
        "latitudeOfFirstGridPointInDegrees": 21.138123, "longitudeOfFirstGridPointInDegrees": 237.280472, "DxInMetres": 3000.0, "DyInMetres": 3000.0}
fwd = hrrr.lcc(proj)
x0, y0 = fwd(proj["latitudeOfFirstGridPointInDegrees"], proj["longitudeOfFirstGridPointInDegrees"])
win = hrrr.crop_window(proj)
ok(win["nx"] > 100 and win["ny"] > 100 and win["i0"] > 0 and win["j0"] > 0, f"crop window {win}")
xc, yc = fwd(39.04456, -84.67229)
ci, cj = (xc - x0) / 3000 - win["i0"], (yc - y0) / 3000 - win["j0"]
ok(round(ci) == 63 and round(cj) == 61, f"CVG falls in crop cell 63,61 (as eccodes found on real data): {ci:.2f},{cj:.2f}")
print(f"ok - {n} HRRR packager checks passed")

# New surface products: accept only the exact height, units and valid instant.
def instant(name, level_type, level, units, fh=3):
    return {'shortName':name,'typeOfLevel':level_type,'level':level,'units':units,'stepType':'instant','stepUnits':1,'startStep':fh,'endStep':fh}
for field,k in [('d2m',instant('2d','heightAboveGround',2,'K')),('u10',instant('10u','heightAboveGround',10,'m s**-1')),('v10',instant('10v','heightAboveGround',10,'m s**-1')),('gust',instant('gust','surface',0,'m s**-1')),('cloud',instant('tcc','atmosphere',0,'%'))]:
    ok(hrrr.classify(k,3)==field,f'{field}: exact GRIB definition')
    ok(hrrr.classify({**k,'endStep':2},3) is None,f'{field}: wrong valid time rejected')
    ok(hrrr.classify({**k,'stepType':'max'},3) is None,f'{field}: interval maximum not instantaneous')
ok(hrrr.classify(instant('2d','heightAboveGround',80,'K'),3) is None,'80-m dew point is not 2-m dew point')
ok(hrrr.classify(instant('10u','heightAboveGround',80,'m s**-1'),3) is None,'wrong wind height rejected')
ok(hrrr.classify(instant('tcc','atmosphere',0,'(0 - 1)'),3) is None,'cloud fractions are not labelled percent')
q=hrrr.encode('d2m',np.array([[273.15,np.nan]]));ok(list(q[0])==[320,hrrr.MISS16],'dew point K to F; missing preserved')
q=hrrr.encode('gust',np.array([[10.,np.nan,-1.]]));ok(list(q[0])==[224,hrrr.MISSU16,hrrr.MISSU16],'gust m/s to mph; invalid negatives missing')
q=hrrr.encode('cloud',np.array([[0.,37.5,100.,np.nan,101.]]));ok(list(q[0])==[0,375,1000,hrrr.MISSU16,hrrr.MISSU16],'cloud percent bounds and missing')
a=np.array([[3.,np.nan]]);b=np.array([[4.,5.]]);m={'startStep':3,'endStep':3,'uvRelativeToGrid':1}
w=hrrr.wind_magnitude(a,b,m,m);ok(w[0,0]==5 and np.isnan(w[0,1]),'wind norm and missing components')
ok(hrrr.wind_magnitude(a,None,m,m) is None,'one wind component missing: speed unavailable')
ok(hrrr.wind_magnitude(a,b,m,{**m,'endStep':4}) is None,'different component times never mixed')
ok(hrrr.wind_magnitude(a,b,m,{**m,'uvRelativeToGrid':0}) is None,'different component reference frames never mixed')
ok(hrrr.encode('w10m',w)[0,0]==112,'derived wind speed packed in mph')
print(f'ok - {n} HRRR packager checks including new fields passed')

# Publishing an expanded product never overwrites or loses a retained legacy URL.
import json,tempfile,hashlib
from unittest.mock import patch
cycle=dt.datetime(2026,11,5,0,tzinfo=U);rid='2026110500';oldrel=f'data/hrrr/{rid}';newrel=f'{oldrel}/{hrrr.PACKAGE}'
oldbytes=b'old immutable temperature';newbytes=b'new expanded temperature';coords=b'coordinates'
def manifest(version,payload):
 return {'schema':1,'version':version,'productVersion':version,'cycle':'2026-11-05T00:00:00Z','hours':[0],
         'fields':{'t2m':{'file':'t2m.bin.gz','sha256':hashlib.sha256(payload).hexdigest(),'missingHours':[]}},'gribBytesDownloaded':1,'browserPayloadBytes':len(payload)+len(coords)}
oldman=manifest('legacy',oldbytes);newman=manifest(hrrr.PACKAGE,newbytes)
oldrec={'id':rid,'cycle':oldman['cycle'],'role':'latest+extended','hoursExpected':48,'hours':1,'path':oldrel}
published={'data/index.json':json.dumps({'runs':[oldrec]}).encode(),oldrel+'/manifest.json':json.dumps(oldman).encode(),oldrel+'/t2m.bin.gz':oldbytes,oldrel+'/latlon.bin.gz':coords}
def mock_http(url,*args,**kwargs):return published[url.removeprefix('https://test/')]
def build_new(c,n,dest,*args,**kwargs):
 os.makedirs(dest,exist_ok=True)
 for name,value in [('t2m.bin.gz',newbytes),('latlon.bin.gz',coords),('manifest.json',json.dumps(newman).encode())]:
  with open(os.path.join(dest,name),'wb') as f:f.write(value)
 return newman
with tempfile.TemporaryDirectory() as root:
 with patch.object(sys,'argv',['hrrr','--site',root,'--published','https://test','--now','2026-11-05T03:00:00+00:00']),patch.object(hrrr,'http',mock_http),patch.object(hrrr,'available',return_value={cycle:full(48)}),patch.object(hrrr,'process_run',side_effect=build_new):hrrr.main()
 with open(os.path.join(root,oldrel,'t2m.bin.gz'),'rb') as f:ok(f.read()==oldbytes,'legacy URL and values remain immutable after upgrade')
 with open(os.path.join(root,newrel,'t2m.bin.gz'),'rb') as f:ok(f.read()==newbytes,'expanded package uses a new versioned URL')
 with open(os.path.join(root,'data/index.json')) as f:newindex=json.load(f)
 ok(newindex['runs'][0]['previousPaths']==[oldrel],'legacy paths tracked for subsequent deploys')
 # Simulate that deployment becoming the published site and verify a carry-only update.
 for dirpath,dirs,files in os.walk(root):
  for name in files:
   with open(os.path.join(dirpath,name),'rb') as f:published[os.path.relpath(os.path.join(dirpath,name),root)]=f.read()
 with tempfile.TemporaryDirectory() as second:
  with patch.object(sys,'argv',['hrrr','--site',second,'--published','https://test','--now','2026-11-05T03:30:00+00:00']),patch.object(hrrr,'http',mock_http),patch.object(hrrr,'available',return_value={cycle:full(48)}),patch.object(hrrr,'process_run',side_effect=AssertionError('must carry, not rebuild')):hrrr.main()
  with open(os.path.join(second,oldrel,'t2m.bin.gz'),'rb') as f:ok(f.read()==oldbytes,'legacy URL survives later carry-only deployment too')
# An upgrade failure must publish only the prior complete package.
published['data/index.json']=json.dumps({'runs':[oldrec]}).encode()
with tempfile.TemporaryDirectory() as root:
 def fail_build(c,n,dest,*args,**kwargs):
  os.makedirs(dest,exist_ok=True)
  with open(os.path.join(dest,'partial'),'w') as f:f.write('bad')
  raise RuntimeError('synthetic failed upgrade')
 with patch.object(sys,'argv',['hrrr','--site',root,'--published','https://test','--now','2026-11-05T03:00:00+00:00']),patch.object(hrrr,'http',mock_http),patch.object(hrrr,'available',return_value={cycle:full(48)}),patch.object(hrrr,'process_run',side_effect=fail_build):hrrr.main()
 ok(not os.path.exists(os.path.join(root,newrel)),'partial upgrade removed from deployment artifact')
 with open(os.path.join(root,'data/index.json')) as f:fallback=json.load(f)
 ok(fallback['runs'][0]['path']==oldrel,'failed upgrade keeps index pointed at working legacy package')
 with open(os.path.join(root,oldrel,'t2m.bin.gz'),'rb') as f:ok(f.read()==oldbytes,'failed upgrade retains exact previous file')
print(f'ok - {n} HRRR packager and atomic-upgrade checks passed')
