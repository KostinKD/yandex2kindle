--[[--
Yandex2Kindle KOReader Plugin
Personal OPDS, quote upload and a shared quote list. Progress uses built-in KOReader sync.
--]]

local util = require("util")
local md5 = require("ffi/sha2").md5
local QuotePayload = require("quote_payload")
local TextViewer = require("ui/widget/textviewer")
local Dispatcher = require("dispatcher")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local logger = require("logger")
local socketutil = require("socketutil")
local http = require("socket.http")
local ltn12 = require("ltn12")
local json = require("json")
local _ = require("gettext")

local Yandex2Kindle = WidgetContainer:extend{
    name = "yandex2kindle",
    is_doc_only = false,
    settings = nil,
}

function Yandex2Kindle:init()
    self.settings = G_reader_settings:readSetting("yandex2kindle", {
        server_url = "http://192.168.1.100:8000",
        username = "",
        password = "",
        quote_auto_sync = true,
    })
    self.ui.menu:registerToMainMenu(self)
    self.settings.quote_auto_sync = self.settings.quote_auto_sync ~= false
end

function Yandex2Kindle:addToMainMenu(menu_items)
    menu_items.yandex2kindle = {
        text = _("Yandex2Kindle Библиотека"),
        sub_item_table = {
            {
                text = _("Синхронизировать цитаты этой книги"),
                callback = function() self:syncQuotes(false) end,
            },
            {
                text = _("Цитаты книги с сайта"),
                callback = function() self:showServerQuotes() end,
            },
            {
                text = _("Логин синхронизации цитат"),
                callback = function() self:editCredential("username", _("Логин синхронизации со страницы Аккаунт")) end,
            },
            {
                text = _("Пароль синхронизации цитат"),
                callback = function() self:editCredential("password", _("Пароль синхронизации")) end,
            },
            {
                text = _("Автоматическая синхронизация цитат"),
                checked_func = function() return self.settings.quote_auto_sync end,
                callback = function()
                    self.settings.quote_auto_sync = not self.settings.quote_auto_sync
                    G_reader_settings:saveSetting("yandex2kindle", self.settings)
                end,
            },
            {
                text = _("Как настроить синхронизацию"),
                callback = function()
                    self:syncCurrentBookProgress()
                end,
            },
            {
                text = _("Открыть OPDS каталог"),
                callback = function()
                    self:openOpdsCatalog()
                end,
            },
            {
                text = _("Настройки сервера"),
                callback = function()
                    self:showSettingsDialog()
                end,
            },
        }
    }
end

function Yandex2Kindle:showSettingsDialog()
    local dialog
    dialog = InputDialog:new{
        title = _("URL сервера Yandex2Kindle"),
        input = self.settings.server_url or "http://192.168.1.100:8000",
        buttons = {
            {
                text = _("Отмена"),
                id = "close",
                callback = function()
                    UIManager:close(dialog)
                end,
            },
            {
                text = _("Сохранить"),
                is_positive = true,
                callback = function()
                    self.settings.server_url = dialog:getInputText()
                    G_reader_settings:saveSetting("yandex2kindle", self.settings)
                    UIManager:close(dialog)
                    UIManager:show(InfoMessage:new{ text = _("Настройки сохранены!") })
                end,
            },
        },
    }
    UIManager:show(dialog)
end

function Yandex2Kindle:openOpdsCatalog()
    local opds_url = (self.settings.server_url or ""):gsub("/+$", "") .. "/opds"
    if Dispatcher:hasAction("opds") then
        Dispatcher:execute("opds", opds_url)
    else
        UIManager:show(InfoMessage:new{
            text = _("OPDS URL:\n" .. opds_url .. "\nДобавьте его в каталоги OPDS."),
        })
    end
end

function Yandex2Kindle:syncCurrentBookProgress()
    UIManager:show(InfoMessage:new{
        text = _("Включите встроенную синхронизацию KOReader: Настройки → Прогресс чтения → Собственный сервер. Укажите URL сервера и учётные данные со страницы «Аккаунт» Yandex2Kindle. Этот плагин открывает OPDS-каталог; отправку прогресса выполняет встроенный модуль KOReader."),
    })
end

function Yandex2Kindle:editCredential(field, title)
    local dialog
    dialog = InputDialog:new{
        title = title,
        input = field == "password" and "" or (self.settings.username or "reader"),
        text_type = field == "password" and "password" or "text",
        buttons = {{
            { text = _("Отмена"), callback = function() UIManager:close(dialog) end },
            { text = _("Сохранить"), callback = function()
                local value = dialog:getInputText()
                if value == "" then return end
                if field == "password" then
                    self.settings.quote_key = md5(value)
                    self.settings.password = nil
                else
                    self.settings.username = value
                end
                G_reader_settings:saveSetting("yandex2kindle", self.settings)
                UIManager:close(dialog)
            end },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function Yandex2Kindle:quoteRequest(document, annotations)
    local base = (self.settings.server_url or ""):gsub("/+$", "")
    if not base:match("^https?://[^@?#]+$") then return nil, _("Проверьте URL сервера") end
    local encoded = #annotations == 0 and json.encode({ document = document })
        or json.encode({ document = document, annotations = annotations })
    local parts, bytes = {}, 0
    local request = {
        url = base .. "/syncs/highlights", method = "POST", redirect = false,
        headers = {
            ["content-type"] = "application/json", ["content-length"] = tostring(#encoded),
            ["x-auth-user"] = self.settings.username, ["x-auth-key"] = self.settings.quote_key,
        },
        source = ltn12.source.string(encoded),
        sink = function(chunk)
            if chunk then
                bytes = bytes + #chunk
                if bytes > 4 * 1024 * 1024 then return nil, "response too large" end
                parts[#parts + 1] = chunk
            end
            return 1
        end,
    }
    local client = base:match("^https://") and require("ssl.https") or http
    socketutil:set_timeout(5, 15)
    local ok, result, code = pcall(client.request, request)
    socketutil:reset_timeout()
    if not ok or not result then return nil, _("Нет соединения с сервером. Цитаты остаются на устройстве.") end
    local decoded_ok, body = pcall(json.decode, table.concat(parts))
    if not decoded_ok or type(body) ~= "table" then return nil, _("Сервер вернул неверный ответ") end
    if tonumber(code) ~= 200 then
        return nil, type(body.detail) == "string" and body.detail or (_("Ошибка синхронизации, HTTP ") .. tostring(code))
    end
    return body
end

function Yandex2Kindle:syncQuotes(silent)
    if self._quote_sync_busy then return end
    if not self.ui.document or not self.ui.annotation then
        if not silent then UIManager:show(InfoMessage:new{text=_("Сначала откройте книгу")}) end
        return
    end
    if not self.settings.quote_key or not self.settings.username then
        if not silent then UIManager:show(InfoMessage:new{text=_("Укажите URL сервера, логин и пароль синхронизации цитат в меню плагина. Они указаны на странице Аккаунт сайта.")}) end
        return
    end
    if silent and not NetworkMgr:isOnline() then return end
    local function run()
        local doc = self.ui.document
        if not doc or not doc.file then return end
        self._quote_sync_busy = true
        local ok, error_message = pcall(function()
            local checksum = util.partialMD5(doc.file)
            if not checksum then error(_("Не удалось определить файл книги")) end
            local quotes = QuotePayload.collect(self.ui.annotation.annotations, md5)
            local total, response = 0, nil
            -- One empty exchange also fetches web quotes for books without local highlights.
            for first = 1, math.max(1, #quotes), 200 do
                local batch = {}
                for index = first, math.min(first + 199, #quotes) do batch[#batch + 1] = quotes[index] end
                local failure
                response, failure = self:quoteRequest(checksum, batch)
                if not response then error(failure) end
                total = total + (response.added or 0)
            end
            if response and self.ui.document == doc and self.ui.doc_settings then
                self.ui.doc_settings:saveSetting("y2k_site_highlights", response.highlights or {})
            end
            if not silent then UIManager:show(InfoMessage:new{text=_("Цитаты синхронизированы. Добавлено на сайт: ") .. tostring(total)}) end
        end)
        self._quote_sync_busy = false
        if not ok then
            logger.warn("Yandex2Kindle quote exchange failed")
            if not silent then UIManager:show(InfoMessage:new{text=tostring(error_message)}) end
        end
    end
    if silent then run() else NetworkMgr:runWhenOnline(run) end
end

function Yandex2Kindle:showServerQuotes()
    local items = self.ui.doc_settings and self.ui.doc_settings:readSetting("y2k_site_highlights") or {}
    local text = QuotePayload.format_quotes(items)
    UIManager:show(TextViewer:new{title=_("Цитаты книги с сайта"),text=text ~= "" and text or _("Сначала выполните синхронизацию цитат этой книги.")})
end

function Yandex2Kindle:onAnnotationsModified()
    if not self.settings.quote_auto_sync then return end
    if self._quote_sync_callback then UIManager:unschedule(self._quote_sync_callback) end
    self._quote_sync_callback = function() self:syncQuotes(true) end
    UIManager:scheduleIn(2, self._quote_sync_callback)
end

function Yandex2Kindle:onReaderReady()
    if self.settings.quote_auto_sync then self:onAnnotationsModified() end
end

function Yandex2Kindle:onNetworkConnected()
    if self.settings.quote_auto_sync then self:onAnnotationsModified() end
end

function Yandex2Kindle:onCloseDocument()
    if self._quote_sync_callback then UIManager:unschedule(self._quote_sync_callback) end
end

return Yandex2Kindle
