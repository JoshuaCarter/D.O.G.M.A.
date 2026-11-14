"""Optional Russian installer pages for the generated FOMOD.

gen_fomod.py calls apply_ru() when fomod/ru.json exists. The first page becomes a language choice
(flag `lang`: en / ru). Every generated page then exists twice: the English one as generated, and a
copy with Russian texts, each visible only for its language. Texts missing from ru.json stay English
(reported on stderr), so a new feature never breaks the build.

fomod/ru.json:
  steps    English page name   -> Russian page name
  groups   English group name  -> Russian group name
  plugins  English plugin name -> {name, desc}   (plugin name = checkbox label as generated)
"""

from __future__ import annotations

import copy
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

XSI = "http://www.w3.org/2001/XMLSchema-instance"
LANG_FLAG = "lang"

LANG_STEP = """<installStep name="Language / Язык">
	<optionalFileGroups order="Explicit">
		<group name="Language / Язык" type="SelectExactlyOne">
			<plugins order="Explicit">
				<plugin name="English">
					<description>Installer language: English.
The pages that follow are shown in English. Game text (MCM menus, tooltips) is installed in English and Russian either way: the game shows the language set in the game settings.</description>
					<conditionFlags>
						<flag name="lang">en</flag>
					</conditionFlags>
					<typeDescriptor>
						<type name="Recommended"/>
					</typeDescriptor>
				</plugin>
				<plugin name="Русский">
					<description>Язык установщика: русский.
Следующие страницы установщика будут показаны на русском языке. Игровой текст (меню MCM, подсказки) устанавливается на английском и русском в любом случае: игра покажет язык, выбранный в настройках игры.</description>
					<conditionFlags>
						<flag name="lang">ru</flag>
					</conditionFlags>
					<typeDescriptor>
						<type name="Optional"/>
					</typeDescriptor>
				</plugin>
			</plugins>
		</group>
	</optionalFileGroups>
</installStep>"""


def _set_visible(step: ET.Element, lang: str) -> None:
    vis = step.find("visible")
    if vis is None:
        vis = ET.Element("visible")
        step.insert(0, vis)
    ET.SubElement(vis, "flagDependency", {"flag": LANG_FLAG, "value": lang})


def _translate_step(step: ET.Element, ru: dict, missing: list[str]) -> None:
    name = step.get("name", "")
    if name in ru.get("steps", {}):
        step.set("name", ru["steps"][name])
    else:
        missing.append(f"page {name!r}")
    for g in step.iter("group"):
        gn = g.get("name", "")
        if gn:
            if gn in ru.get("groups", {}):
                g.set("name", ru["groups"][gn])
            else:
                missing.append(f"group {gn!r}")
    for p in step.iter("plugin"):
        pn = p.get("name", "")
        tr = ru.get("plugins", {}).get(pn)
        if not tr:
            missing.append(f"plugin {pn!r}")
            continue
        p.set("name", tr["name"])
        d = p.find("description")
        if d is not None and tr.get("desc"):
            d.text = tr["desc"]


def apply_ru(xml: str, ru_path: Path) -> str:
    ru = json.loads(ru_path.read_text(encoding="utf-8"))
    ET.register_namespace("xsi", XSI)
    # ET drops the XML declaration's encoding line; re-added below.
    root = ET.fromstring(xml.encode("utf-8"))
    steps = root.find("installSteps")
    if steps is None:
        raise ValueError("ModuleConfig has no installSteps")
    old = list(steps)
    for s in old:
        steps.remove(s)
    steps.append(ET.fromstring(LANG_STEP))
    missing: list[str] = []
    for s in old:
        en, rs = copy.deepcopy(s), copy.deepcopy(s)
        _set_visible(en, "en")
        _set_visible(rs, "ru")
        _translate_step(rs, ru, missing)
        steps.append(en)
        steps.append(rs)
    if missing:
        print("fomod_ru: no Russian text for (left in English): " + ", ".join(dict.fromkeys(missing)), file=sys.stderr)
    ET.indent(root, space="\t")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n"
