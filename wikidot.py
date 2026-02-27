from playwright.async_api import async_playwright,Browser,\
    BrowserContext,Page,Response,Route,Request,TimeoutError as pw_TimeoutError
from typing import Optional,Callable,Awaitable
from urllib import parse
from contextlib import asynccontextmanager
from functools import partial
import re,dataclasses,asyncio
from urllib.parse import parse_qsl
from bs4 import BeautifulSoup #type:ignore
class WikidotError(Exception):pass
class WikidotSession:
    @dataclasses.dataclass
    class PageVersion:
        id:int
        ts:int
        editor_name:str
        editor_id:int
        summary:str
    
    TIMEOUT=5000
    def __init__(self,context:BrowserContext):
        self.context:BrowserContext=context
    @asynccontextmanager
    async def _init_page(self):
        assert self.context
        async def my_route(route:Route):
            if route.request.resource_type in (
                "image",
                "media",
                "stylesheet",
                "font"
            ):
                await route.fulfill(status=200,body=bytes())
                return 
            pr=parse.urlparse(route.request.url)
            allowed_domains = [
                "wikidot.com",
                # "wdfiles.com", # site files
                # "d2qhngyckgiutd.cloudfront.net", # avatar
                "d3g0gp89917ko0.cloudfront.net" # script,stylesheet,etc.
            ]
            pr = parse.urlparse(route.request.url)
            if not pr.hostname or not any(domain in pr.hostname for domain in allowed_domains):
                await route.abort()
                return 
            await route.continue_()
        
        try:
            page=await self.context.new_page()
            await page.route("**/*",my_route)
            yield page
        finally:
            # await asyncio.sleep(100000)
            await page.close()
    @asynccontextmanager
    async def wd_page(self,p):
        if isinstance(p,str):
            async with self._init_page()as page:
                await page.goto(p)
                yield page
        elif isinstance(p,Page):
            yield p
    def _make_module_predicate(self,**kwargs):
        def func(resp:Response):
            if not (resp.url.endswith("ajax-module-connector.php") and resp.request.post_data):
                return False
            form=dict(parse_qsl(resp.request.post_data))
            return all(form[k]==str(v) for (k,v) in kwargs.items())
        return func
    def _make_module_expect(self,page:Page,name,**kwargs):
        return page.expect_response(self._make_module_predicate(moduleName=name,**kwargs),timeout=self.TIMEOUT)
    
    async def login(self,username,password):
        async with self._init_page()as page:
            await page.goto("https://www.wikidot.com/default--flow/login__LoginPopupScreen")
            await page.get_by_role("textbox", name="username or email address")\
                .fill(username)
            await page.get_by_role("textbox", name="password")\
                .fill(password)
            try:
                async with page.expect_request("http://www.wikidot.com/afterlogin.php",timeout=self.TIMEOUT)as req:
                    await page.get_by_role("button", name=" Sign in").click()
                await req.value
            except pw_TimeoutError as e:
                raise WikidotError("Failed to login")from e
    async def get_page_source(self,p:str|Page):
        async with self.wd_page(p)as page:
            try:
                async with self._make_module_expect(page,"viewsource/ViewSourceModule")as e:
                    await page.evaluate("WIKIDOT.page.listeners.viewSourceClick();")
                resp=await e.value
                data=await resp.json()
                if (s:=data.get("status"))!="ok":
                    raise WikidotError(f"Faield to get content of {p} with status {s}:{data['message']}")
                assert(ele:=BeautifulSoup(data["body"],"lxml").find(class_="page-source"))
                return ele.text
            except pw_TimeoutError as e:
                raise WikidotError(f"Failed to get content of {p}")from e

    async def get_page_history(self, p:str|Page):
        async with self.wd_page(p) as page:
            await page.evaluate("WIKIDOT.page.listeners.historyClick()")
            se: Response = await page.wait_for_event("response", self._make_module_predicate(moduleName="history/PageHistoryModule"), timeout=self.TIMEOUT)
            data = await se.json()
            if (s := data.get("status")) != "ok":
                raise WikidotError(f"Failed to get history of {p} with status {s}:{data['message']}")
            await page.wait_for_function("typeof window.updatePagedList !== 'undefined'")
            for i in range(1, 2**99):
                await page.evaluate(f"updatePagedList({i})")
                de: Response = await page.wait_for_event("response", self._make_module_predicate(moduleName="history/PageRevisionListModule",page=i), timeout=self.TIMEOUT)
                data = await de.json()
                soup = BeautifulSoup(data["body"], "lxml")
                rs = soup.find_all("tr", id=re.compile(r"^revision-row-\d+"))
                if len(rs) == 0:
                    break
                    
                for tr_element in rs:
                    tds = tr_element.find_all("td")
                    if len(tds) < 7:
                        continue
                    
                    # 1. id 从 tr 的 id 属性中提取长数字
                    match = re.search(r'revision-row-(\d+)', tr_element.get('id', ''))
                    revision_id = int(match.group(1)) if match else 0
                    
                    # 2. 时间戳从 odate 元素中提取
                    odate_element = tds[5].find("span", class_="odate")
                    timestamp = 0
                    if odate_element:
                        odate_class = odate_element.get("class", [])
                        for cls in odate_class:
                            if cls.startswith("time_"):
                                try:
                                    timestamp = int(cls.replace("time_", ""))
                                    break
                                except ValueError:
                                    pass
                    
                    editor_td = tds[4]  # 第5个td包含编辑器信息
                    printuser = editor_td.find("span", class_="printuser")
                    editor_name = "Unknown"
                    editor_id = 0
                    
                    if printuser:
                        editor_name = printuser.text.strip()
                        name_link = printuser.find("a", href=re.compile(r"user:info"))
                        if name_link:
                            onclick = name_link.get('onclick', '')
                            editor_id_match = re.search(r'userInfo\((\d+)\)', onclick)
                            if editor_id_match:
                                editor_id = int(editor_id_match.group(1))
                    
                    summary_td = tds[6] if len(tds) > 6 else None
                    summary = summary_td.text.strip() if summary_td else ""
                    
                    yield self.PageVersion(
                        id=revision_id,
                        ts=timestamp,
                        editor_name=editor_name,
                        editor_id=editor_id,
                        summary=summary
                    )
                
                
    async def get_revision_source(self,p,rid):
        async with self.wd_page(p)as page:
            if (await page.locator("#history-form-1").count())==0:
                print(1)
                await anext(it:=self.get_page_history(page))
                await it.aclose()
            async with self._make_module_expect(page,"history/PageSourceModule",revision_id=rid)as e:
                await page.evaluate(f"showSource({rid})")
            data=await (await e.value).json()
            if (s := data.get("status")) != "ok":
                raise WikidotError(f"Failed to get revision source of {p}/{rid} with status {s}:{data['message']}")
            assert(ele:=BeautifulSoup(data["body"],"lxml").find(class_="page-source"))
            return ele.text
