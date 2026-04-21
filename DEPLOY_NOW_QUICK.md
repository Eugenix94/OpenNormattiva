# ⚡ QUICK DEPLOY (1 minute)

Everything is ready. Here's the fastest path to deployment:

## Step 1: Get Token (2 minutes)
Visit: https://huggingface.co/settings/tokens

- Click "New token"
- Name: `normattiva`
- Role: `write` ← important!
- Generate & Copy

## Step 2: Deploy (paste into PowerShell)

```powershell
$env:HF_TOKEN = "hf_paste_your_token_here"
python run_deployment.py
```

## Step 3: Wait (3-5 minutes)

Your Space is building at:
```
https://huggingface.co/spaces/YOUR_USERNAME/normattiva-search
```

---

## What You Get

✅ 157,121 searchable Italian laws  
✅ Full-text search engine  
✅ Citation graphs  
✅ Domain categorization  
✅ Live update tracking  
✅ Public dataset  

---

## If Something Goes Wrong

**Check logs:**
```
https://huggingface.co/spaces/YOUR_USERNAME/normattiva-search/logs
```

**Common fixes:**
- Token needs "write" role
- Wait a bit longer (up to 10 min for first build)
- Check internet connection

---

That's it! 🚀
