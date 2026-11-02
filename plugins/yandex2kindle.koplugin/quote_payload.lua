local M = {}

local function position(value)
    if type(value) == 'string' then return value end
    if type(value) == 'table' then
        return table.concat({tostring(value.page or ''), tostring(value.x or ''), tostring(value.y or '')}, ':')
    end
    return ''
end

function M.collect(raw, hash)
    local quotes = {}
    for _, item in ipairs(raw or {}) do
        if item.drawer and type(item.text) == 'string' and item.text:match('%S') then
            local start = position(item.pos0)
            local stamp = tostring(item.datetime or '')
            if start ~= '' then
                -- KOReader persists custom fields with the annotation in its sidecar.
                -- Keep the first identity when the user adjusts either boundary later.
                item.y2k_quote_id = item.y2k_quote_id or hash(stamp .. '|' .. start .. '|' .. position(item.pos1) .. '|' .. item.text)
                table.insert(quotes, {
                    key = item.y2k_quote_id, text = item.text,
                    comment = type(item.note) == 'string' and item.note or '',
                    cfi = type(item.epubcfi) == 'string' and item.epubcfi:match('^epubcfi%(') and item.epubcfi or '',
                    color = type(item.color) == 'string' and item.color or 'yellow',
                })
            end
        end
    end
    return quotes
end

function M.format_quotes(items)
    local lines = {}
    for index, item in ipairs(items or {}) do
        table.insert(lines, tostring(index) .. '. ' .. (item.text or ''))
        if item.comment and item.comment ~= '' then table.insert(lines, item.comment) end
        table.insert(lines, '')
    end
    return table.concat(lines, '\n')
end

return M
