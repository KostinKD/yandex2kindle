package.path = './plugins/yandex2kindle.koplugin/?.lua;' .. package.path
local payload = require('quote_payload')
local function identity(value) return value end
local raw = {
    {text='Bookmark', datetime='2026-01-01', pos0='/p1'},
    {drawer='lighten', text='Quote', note='Note', datetime='2026-01-02', pos0='/p2', epubcfi='epubcfi(range)'},
    {drawer='lighten', text='', datetime='2026-01-03', pos0='/p3'},
}
local items = payload.collect(raw, identity)
assert(#items == 1, 'position-only bookmarks and empty selections must not be uploaded')
assert(items[1].text == 'Quote' and items[1].comment == 'Note')
assert(items[1].key == '2026-01-02|/p2||Quote', 'initial identity includes the selection, not its editable note')
assert(items[1].cfi == 'epubcfi(range)', 'use a range CFI supplied by KOReader')
raw[2].pos0 = '/p9'
raw[2].text = 'Adjusted quote'
local moved = payload.collect(raw, identity)
assert(moved[1].key == items[1].key, 'editing selection boundaries must retain the quote identity and tombstone')
local older = payload.collect({{drawer='lighten',text='Older',pos0={page=12,x=1,y=2},datetime='2026-01-01'}}, identity)
assert(#older == 1 and older[1].cfi == '', 'older/PDF engines must not invent an EPUB position')
local cache = payload.format_quotes({{text='From the site',comment='A note'}})
assert(cache:find('From the site',1,true) and cache:find('A note',1,true))
print('quote payload: 7 checks passed')
