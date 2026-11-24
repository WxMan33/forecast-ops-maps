#!/usr/bin/env python3
"""Upload a packed CONUS site (pipeline/conus.py --pack) to a Cloudflare R2 bucket over its S3 API, and remove runs
that are no longer retained. Credentials come from the environment (GitHub Actions secrets), never from files:

  R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   bucket-scoped R2 API token (Object Read & Write, one bucket)
  R2_ACCOUNT_ID                            the account part of https://<account>.r2.cloudflarestorage.com
  R2_BUCKET                                e.g. forecast-ops-conus-pilot

Packed hour files are immutable (cached for a year); manifests and the index are re-read on every visit (no-cache).
An object already in the bucket with the same size is not uploaded again; a manifest or index (.json) is re-uploaded
only when its contents changed (its MD5 differs from the bucket ETag). Prints what it did: objects and bytes
uploaded, deleted, and kept, so the pilot can report storage and request counts. --dry-run lists the plan only.
"""
import argparse, hashlib, json, mimetypes, os, sys


def plan(site, existing):
    """existing: {key: size} or {key: (size, etag)}. Returns (uploads [(key, path, size, cache)], deletes [key], kept [key]).
    Immutable files are kept when the bucket has the same size; .json files when the bucket's ETag (the MD5 of a single
    PUT) equals the local file's MD5, so an unchanged manifest costs no Class A operation."""
    root = os.path.join(site, "data", "conus"); files = {}
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n); key = os.path.relpath(p, site).replace(os.sep, "/"); files[key] = p
    # each model prunes only its own runs, and only when this build carries that model's run list: a model whose build
    # failed (no index here) keeps everything it already has in the bucket
    prunable, retained = [], set()
    for model, idx in (("hrrr", os.path.join(root, "index.json")), ("nbm", os.path.join(root, "nbm", "index.json")), ("rrfs", os.path.join(root, "rrfs", "index.json"))):
        if not os.path.exists(idx): continue
        prunable.append(f"data/conus/{model}/")
        with open(idx) as f:
            for r in json.load(f)["runs"]: retained.add(r["path"].rstrip("/") + "/")
    uploads, kept = [], []
    for key, p in sorted(files.items()):
        size, mutable = os.path.getsize(p), key.endswith(".json")
        have = existing.get(key); have_size, etag = (have if isinstance(have, tuple) else (have, None))
        if not mutable and have_size == size: kept.append(key); continue
        if mutable and etag and have_size == size:
            with open(p, "rb") as fp:
                if hashlib.md5(fp.read()).hexdigest() == etag: kept.append(key); continue
        uploads.append((key, p, size, "no-cache" if mutable else "public, max-age=31536000, immutable"))
    deletes = sorted(k for k in existing if any(k.startswith(m) for m in prunable) and k not in files and not any(k.startswith(r) for r in retained))
    return uploads, deletes, kept


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--site", required=True); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    if a.dry_run:
        up, de, ke = plan(a.site, {}); print(json.dumps({"dryRun": True, "uploads": len(up), "bytes": sum(u[2] for u in up)})); return
    import boto3
    env = {k: os.environ.get(k) for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID", "R2_BUCKET")}
    missing = [k for k, v in env.items() if not v]
    if missing: sys.exit("missing settings: " + ", ".join(missing))
    s3 = boto3.client("s3", endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com", aws_access_key_id=env["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"], region_name="auto")
    existing, lists = {}, 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=env["R2_BUCKET"], Prefix="data/conus/"):
        lists += 1
        for o in page.get("Contents", []): existing[o["Key"]] = (o["Size"], (o.get("ETag") or "").strip('"'))
    uploads, deletes, kept = plan(a.site, existing)
    # immutable data first, the index last, so a visitor never sees an index pointing at files not yet there
    uploads.sort(key=lambda u: (u[0].endswith("index.json"), u[0].endswith("manifest.json")))
    for key, p, size, cache in uploads:
        with open(p, "rb") as f:
            s3.put_object(Bucket=env["R2_BUCKET"], Key=key, Body=f, ContentType=mimetypes.guess_type(key)[0] or "application/octet-stream", CacheControl=cache)
    for i in range(0, len(deletes), 1000):
        s3.delete_objects(Bucket=env["R2_BUCKET"], Delete={"Objects": [{"Key": k} for k in deletes[i:i + 1000]]})
    after = {k: v[0] for k, v in existing.items() if k not in deletes}; after.update({u[0]: u[2] for u in uploads}); stored = sum(after.values())
    print(json.dumps({"uploaded": len(uploads), "uploadedBytes": sum(u[2] for u in uploads), "deleted": len(deletes), "kept": len(kept),
                      "classA": {"put": len(uploads), "list": lists, "deleteBatches": (len(deletes) + 999) // 1000}, "storedBytesAfter": stored}))


if __name__ == "__main__":
    main()
