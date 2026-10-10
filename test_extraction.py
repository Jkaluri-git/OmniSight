from omnisight.vlm.extract import extract_code_blocks, extract_json, fix_from_code_blocks


def test_fenced_json():
    assert extract_json('Sure!\n```json\n{"findings": []}\n```') == {"findings": []}

def test_json_inside_prose_with_trailing_comma():
    assert extract_json('Here you go: {"a": [1,2,], "b": "x}y",} thanks')["b"] == "x}y"

def test_no_json():
    assert extract_json("nothing here") is None

def test_code_blocks_isolated_from_prose():
    txt = "The button clips.\n```css\n.btn { margin-left: 0 }\n```\nAlso:\n```html\n<div class=\"w-full\"></div>\n```"
    blocks = extract_code_blocks(txt)
    assert [b["lang"] for b in blocks] == ["css", "html"] and "margin-left" in blocks[0]["code"]

def test_fix_from_code_block_fallback_with_media():
    fx = fix_from_code_blocks("```css\n@media (max-width: 480px) { .cta { margin-left: 0; } }\n```")
    assert fx.selector == ".cta" and fx.media == "(max-width: 480px)" and "margin-left" in fx.css
