param(
    [string]$NasHost = "kai@192.168.10.104",
    [string]$SshKey = "D:/agent/ssh/fnos",
    [string]$Snapshot = ".archify/tokbrain-nas-deploy/douyin_rag.db"
)

$ErrorActionPreference = "Stop"
python scripts/export_tokbrain_snapshot.py --output $Snapshot
if ($LASTEXITCODE -ne 0) { throw "snapshot export failed" }

$remoteTemp = "/vol1/1000/tokbrain/data/douyin_rag.db.new"
$remoteLive = "/vol1/1000/tokbrain/data/douyin_rag.db"
scp -i $SshKey -o BatchMode=yes $Snapshot "${NasHost}:$remoteTemp"
if ($LASTEXITCODE -ne 0) { throw "snapshot upload failed" }

ssh -i $SshKey -o BatchMode=yes $NasHost `
    "python3 -c 'import sqlite3; c=sqlite3.connect(`"file:$remoteTemp`?mode=ro`",uri=True); assert c.execute(`"pragma integrity_check`" ).fetchone()[0]==`"ok`"; print(c.execute(`"select count(*) from works where library_state=`'in_library`'`" ).fetchone()[0],c.execute(`"select count(*) from knowledge_chunks`" ).fetchone()[0])' && chmod 400 $remoteTemp && cp -p $remoteLive $remoteLive.bak && mv $remoteTemp $remoteLive"
if ($LASTEXITCODE -ne 0) { throw "remote verification or activation failed" }

Write-Host "TokBrain NAS snapshot activated. Previous snapshot: $remoteLive.bak"
