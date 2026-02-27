import wikidot
import asyncio
import time
import hashlib
async def batch_exec(lst,batch_size=24):
    res=[]
    batch_size=24
    for i in range(0,len(lst),batch_size):
        res.extend(await asyncio.gather(*lst[i:i+batch_size]))
    return res
async def main():
    from playwright.async_api import async_playwright
    async with async_playwright()as playwright:
        browser=await playwright.chromium.launch(headless=True)
        ctx=await browser.new_context()
        sess=wikidot.WikidotSession(ctx)
        st=time.perf_counter_ns()
        await sess.login("PaperBot","hJ}=W4}Pb?v=bPH")
        async with sess.wd_page("https://write-on-paper.wikidot.com/")as p:
            lst=[]
            async for i in sess.get_page_history(p):
                lst.append(sess.get_revision_source(p,i.id))
            res=await batch_exec(lst)
            for i in res:
                print(hashlib.sha256(i.encode()).hexdigest()[-6:])
        print(round((time.perf_counter_ns()-st)/1e6),"ms")
        await browser.close()
if __name__=="__main__":
    asyncio.run(main())