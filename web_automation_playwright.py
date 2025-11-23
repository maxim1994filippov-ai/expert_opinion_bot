# web_automation_playwright.py
import asyncio
import logging
from typing import List, Dict, Optional
from playwright.async_api import async_playwright, Page

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class PlaywrightExpertBot:
    LOGIN_URL = "https://panel.expertnoemnenie.ru/login"
    SURVEYS_URL = "https://panel.expertnoemnenie.ru/surveys"

    def __init__(self, email: str, password: str, headless: bool = True):
        self.email = email
        self.password = password
        self.browser = None
        self.context = None
        self.page: Optional[Page] = None
        self.playwright = None
        self.headless = headless

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless, args=["--no-sandbox"])
        self.context = await self.browser.new_context(viewport={"width":1280, "height":800})
        self.page = await self.context.new_page()
        logging.info("Playwright started")

    async def stop(self):
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logging.warning("Error stopping playwright: %s", e)

    async def login(self) -> bool:
        if not self.page:
            await self.start()

        logging.info("Navigating to login page")
        await self.page.goto(self.LOGIN_URL, wait_until="domcontentloaded")
        try:
            await self.page.wait_for_selector('input[name="email"]', timeout=15000)
            await self.page.fill('input[name="email"]', self.email)
            await self.page.fill('input[name="password"]', self.password)
            await self.page.click("//button[contains(text(), 'Войти')]")
            await self.page.wait_for_url(lambda url: "/surveys" in url.path, timeout=20000)
            logging.info("Login successful, current url: %s", self.page.url)
            return True
        except Exception as e:
            logging.error("Login failed: %s", e, exc_info=True)
            return False

    async def get_available_surveys(self) -> List[Dict]:
        if not self.page:
            raise RuntimeError("Playwright page not started")

        await self.page.goto(self.SURVEYS_URL, wait_until="networkidle")
        await asyncio.sleep(2)

        surveys = []
        try:
            buttons = await self.page.locator("//button[contains(text(), 'Пройти опрос')]").all()
            if not buttons:
                logging.info("No 'Пройти опрос' buttons found")
                return surveys

            for i, btn in enumerate(buttons):
                try:
                    card = btn.locator("xpath=ancestor::div[contains(@class, 'card')][1]")
                    title_el = card.locator(".//h5")
                    title = (await title_el.inner_text()) if await title_el.count() else await card.inner_text()
                    text = await card.inner_text()
                    import re
                    points_match = re.search(r"(\d+)\s*балл", text)
                    duration_match = re.search(r"(\d+)\s*минут", text)
                    points = int(points_match.group(1)) if points_match else 0
                    duration = duration_match.group(1) if duration_match else "N/A"
                    xpath_btn = f"(//button[contains(text(), 'Пройти опрос')])[{i+1}]"

                    surveys.append({
                        "index": i,
                        "title": title.strip() if isinstance(title, str) else "Опрос",
                        "points": points,
                        "duration": duration,
                        "button_xpath": xpath_btn
                    })
                except Exception as e:
                    logging.warning("Error parsing survey card: %s", e)
                    continue

            return surveys
        except Exception as e:
            logging.error("Error getting surveys: %s", e, exc_info=True)
            return surveys

    async def open_survey_by_xpath(self, button_xpath: str) -> bool:
        if not self.page:
            raise RuntimeError("page not started")
        try:
            logging.info("Clicking survey button: %s", button_xpath)
            await self.page.click(button_xpath)
            await asyncio.sleep(2)
            return True
        except Exception as e:
            logging.error("Error clicking survey button: %s", e, exc_info=True)
            return False

    async def check_captcha(self) -> bool:
        if not self.page:
            return False
        try:
            frames = self.page.frames
            for f in frames:
                src = f.url or ""
                if "recaptcha" in src or "google.com/recaptcha" in src:
                    logging.info("Detected reCAPTCHA iframe: %s", src)
                    return True
            iframe_count = await self.page.locator("iframe").count()
            for i in range(iframe_count):
                src = await self.page.locator("iframe").nth(i).get_attribute("src")
                title = await self.page.locator("iframe").nth(i).get_attribute("title")
                if src and "recaptcha" in src:
                    logging.info("Detected recaptcha src iframe")
                    return True
                if title and "recaptcha" in title.lower():
                    logging.info("Detected recaptcha title iframe")
                    return True
            return False
        except Exception as e:
            logging.warning("Error checking captcha: %s", e)
            return False

    async def screenshot_captcha(self, path: str) -> str:
        if not self.page:
            raise RuntimeError("page not started")
        try:
            iframe_locator = self.page.locator("iframe")
            count = await iframe_locator.count()
            for i in range(count):
                src = await iframe_locator.nth(i).get_attribute("src") or ""
                title = await iframe_locator.nth(i).get_attribute("title") or ""
                if "recaptcha" in src or "recaptcha" in title.lower():
                    await self.page.screenshot(path=path, full_page=True)
                    return path
            await self.page.screenshot(path=path, full_page=True)
            return path
        except Exception as e:
            logging.error("Error making captcha screenshot: %s", e, exc_info=True)
            await self.page.screenshot(path=path, full_page=True)
            return path

    async def continue_after_captcha(self) -> bool:
        if not self.page:
            raise RuntimeError("page not started")
        try:
            await self.page.wait_for_timeout(1500)
            return True
        except Exception as e:
            logging.warning("Continue after captcha error: %s", e)
            return False
