"""
Hand-rolled, stdlib-only PDF builder for the consolidated guest check-in
ticket attached to the registration confirmation email.

This module is COPIED into every Lambda that needs it — this host's own
(register, manage_registrations, my_registrations) and, separately, the
seat-selection plugin's copy at plugins/seat-selection/backend/checkin_pdf.py
(kept plugin-local rather than referenced from here, since that plugin is
designed to be mounted in a different host app too — see its README). Keep
the copies in sync.

Deliberately avoids any PDF/imaging library: this project's Lambda layers
don't bundle Pillow (see qr_email.py's own PNG encoder for the same
reasoning), so this writes a minimal, valid PDF 1.4 file by hand — one
page, PDF's built-in Helvetica/Helvetica-Bold/Helvetica-Oblique base fonts
(no font embedding needed, so non-Latin1 text — e.g. CJK names — falls
back to "?" rather than rendering, a known limitation of this approach),
and raster images (logo + QR, plus any configured sponsor logos) as Image
XObjects carrying raw zlib-compressed pixel data directly (no PNG
container involved at all).

The decorative border, status pills, and venue seat map are all pure
vector paths (rect/rounded-rect/circle/diamond/bezier), built from the
same handful of _Content primitives — no image involved for any of that.

The WMF Japan logo (frontend/public/logo.png) is baked in below as a
pre-shrunk, pre-compressed raw-RGB blob rather than decoded from a PNG at
runtime, so no PNG-decoding code is needed here either. To refresh it
after the logo image changes, regenerate _LOGO_RGB_B64 with:

    from PIL import Image
    import zlib, base64
    im = Image.open("frontend/public/logo.png").convert("RGBA")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, mask=im.split()[3])
    w = 180
    bg = bg.resize((w, round(bg.height * w / bg.width)), Image.LANCZOS)
    print(bg.size)  # update _LOGO_W, _LOGO_H below to match
    print(base64.b64encode(zlib.compress(bg.tobytes(), 9)).decode())
"""

import base64
import logging
import math
import re
import zlib

logger = logging.getLogger(__name__)


PLACEHOLDER = "<!-- REGISTRATION_QR -->"

PAGE_W, PAGE_H = 612, 792  # US Letter, in points

_LOGO_W, _LOGO_H = 180, 98

_LOGO_RGB_B64 = (
    "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"
    "LthzSfzVT2KDgB1HwK5k/DEgxFTKPpDzAoGFf8YGJLEDIATgn3+maUicD7is+UMXhGSvJJMhcQyRUwj5M2xEpvosSDD4Fhsg"
    "DIJkAyD4v5VB6F/YFP+1uYH+DhufK//UgP1zvA1d7pcGdxeVtzzOb7+282P4xjxZkzsUxdLZKp0LtYE4Odxa4YQrPRwvxX79"
    "PtTDfPUovViKpT7lR7N5GrEhcpcvby16ef9NjhH4+rIozZCzQZG5XJEdNpW9ZBZ7qTQrdCJ7mTwrbBL+yYxSw9YqH96lY7PP"
    "2CfRYsE25Z4aM2JcrzoA6r6AwDzwvZ5I2NfYktP03Kyh6wuNVgNUWMLkKA/Qmgg0xUoWi0IGMjFR4R88mr8oL8gX41898H+h"
    "nCC/yhPyLTZw3tD6O7yB/jJbkP+hgnb2Qt/uvX3bE/pT07hlLxEA+FZwDH8ZOHuxN34LdfP2/qRkib4xjI06KTleDWG4CRqb"
    "6BlXe/fso+5M6Es8yLz7EOihfqvQ/kFLJHmWQIjz/BX9eCpjz0FW4raB1E2Cigohtafj2qr82Sq3KXM/melhCUrVptPKHaOq"
    "P1zSp1DsKFI2Ct+nX03uauvAiu15KQc5CTGsrQY9h08zMucDtFqoabvw3Tb+0wPCuw7C0jPCLFPx+zxhkQ3Q8R6jnU46P80u"
    "wcZvv47XIbeeChOM/xbifAU+WZIenJNvS40Ntlh47DXWjRl3ruQcJirlf3Ui03YhosZIpJ+YL8kUCB6/3nOz5NClJ8fTC1Ny"
    "391uodZ9a7QO91cvo/N0zt5LT5JPPNojmY49rA0OH8kTcd7WPbtSnFr08eHwAcOHgRD4uflN1sv0iwVJlwuTc97eaOiuGj4D"
    "+k3GKgIbJ728duu47VLbdZXAhlAsuFaSdv7xkVM5e4s+Pfi1H+yPC6ewsMPDq37qtPrxE2snTambOq1u/KTaSZNazC0YVzPR"
    "b+FBXrXVwanup9E1Y8c16egjRLA31rFtR/P0mQ0ysk3KqsxnZX3xG+tnStVNmFg3YVLtePxzcu24SQ2yCtRNm4Hunj+Ax9CN"
    "MzOvt5la1k0if4W3ZOzkqu/GV46d1GxsyM2LF3PF74LW3KTMqnXSK541h/3ozFq/OYpTZK9dThIJN6IFRe1WlpVjZlR+N6by"
    "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"
)
# ── Iroha row-label glyphs (baked bitmaps) ───────────────────────────────
#
# The venue's own row lettering uses the classic iroha ordering (い, ろ,
# は, ...) rather than plain numbers — seat_purchase_emailer.py's own
# _seat_display already shows it in the confirmation email. Helvetica's
# WinAnsiEncoding can't draw these characters (see the module docstring),
# so — same trick as the logo above — each of the 19 characters actually
# used (one per row) is pre-rendered as a small grayscale bitmap and baked
# in here; _iroha_glyph_object() below turns one into an Image XObject on
# demand wherever a seat annotation needs it. To regenerate after a row
# label ever changes:
#
#     from PIL import Image, ImageDraw, ImageFont
#     import zlib, base64
#     font_size = 96  # update _IROHA_GLYPH_W/H/DESCENT_FRAC below to match
#     font = ImageFont.truetype("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", font_size)
#     ascent, descent = font.getmetrics()
#     w, h = font_size, ascent + descent
#     print(w, h, descent / h)
#     im = Image.new("L", (w, h), 255)
#     draw = ImageDraw.Draw(im)
#     draw.text((0, 0), ch, font=font, fill=0)  # (0, 0) puts the baseline at a
#         # fixed y=ascent for every character, so they all share one baseline
#     print(base64.b64encode(zlib.compress(im.tobytes(), 9)).decode())
_IROHA_GLYPH_W = 96
_IROHA_GLYPH_H = 97
_IROHA_GLYPH_DESCENT_FRAC = 0.12371134020618557

_IROHA_GLYPHS_B64 = {
    'い': (
        'eNrtmF1IU1EcwK9rm86me8gltHQGUSERaVk9WUi+RMSCSErtO8vKxHww+qAgIoKweowyktSHKCjKyiAqIulTyr5Y0mq9mE634ddM'
        'p7f0/v9rX/fcc+7u6uX+37z+zu+/c7dz/v9zeF4NNdRQQw011PiP0ckJkRUnfxf4rXHyu8CfGSd/L/gz4uR3g38myyDnfgct2gd+'
        'C73dsVPH5fgoYR/4Z9DaO7ZqJ/htlLgf/GZKvnoKDLhIOUAj4CZK/F4C+JPe0g1IBJz2/VSCn8um+wpSBVpD6x9egAn2UfHpQPtp'
        'E3xIwgR3afBZAA9R/z7PoD/dRUFnA9xD7R9bhgk2UtCLgXXSL7BPiZjgjjS8AtCPDPvDKfRn9EmyawB9weD352KCckm2FMiHLBvc'
        'c1iVXMJLKXQvkLeYtt0ynMCScQnyEID1bGUjDRNcpvw117LVjTr0T/eSwSvAHWbzj+dhgioyeBuw3YyF7wn69d+JXCtg61grqw0T'
        'bCJiX4HKZ/V/0cFITTsJGwJqNnNrUIETWE3ETAJkYPb/TMYErSRsHkBu5gTV6F9FolYC9J7Z3z0VE7whUDvo99rwqEH/WgJ0Apiz'
        '7P4eI25zhMlfBWaPjO4y0EyUEvZaQApl+J1aXMSdoownlhNAMU7gqDhjBsQjw/8O/eZhUSYfkEdyJlCICeok13mtHP9N9C+ULBUl'
        'cvz+TEzwSgxpIxzByiW75JPoLxMjRnCfijz0eHUGqbrcpYfRKQNiSIFoqW6aKGy/yAk24AQuiRHHACiK+E/RZAvyg+hvgdHa02LE'
        'Y5xh+CftFzbItAfEdtci1Fe7KDFqEmno6+H5tDFSgoN/iAJiH7depFLj4tlFfEGfuZz75K+oCTzJoe2wA7vMZ+ThLVI94iDu4xdC'
        'HlfhSS7mW4LNYJob/KL78Ws5F7M/0IxdC3p4HJ4ZvbFfcyzCLujvobY7BZ5VKHCN0oATOBKxLrXfFPCPzsFmD9uIRsy4RZF7oBuo'
        'MzZP/t2MZ0S9QxE/vxwTaLa/HrAfwMLNVSp0kdVh4KKF2aOQnz8f1d+g3FVccRS9TcGrPt/SCH2WW0E/3zs/TG9qV/ay0pUXok99'
        'qvRt6GBJkN7axisf162BZeDm4xEjjTaL1phbY+fVUEMNNdT41/EbAJsp0w=='
    ),
    'ろ': (
        'eNr7/38UjIJRMApGATr4+5Q25r45v3FKeaSNPAtDK/UM/Xj7yJqptWl+ZvIcDHDAuIpc436/f3Tt1L6NS6c0FcT5WGmIsjBgBZwn'
        'yTF8tQg7A5FA/CEZ5t9nIB7ofiLDAgXizRe8SYb5SUQbz3mUnAhYittAPtPY9o35MB7LZrJSz3MsBgsbh1XOOfgMJL2TGSY4j8zk'
        'qQk3lkPJNqp86uZLSNF4nR8m2U5u8q/UcY4s6Fiw89IbTLm3KjDj82lROvxyhBkf+Y8W5qfBjHf9RQvj+2HGm3ymhfHrmaDGq76i'
        'hfGnuKDGS9yjhfH3xWG57AItjH8Pyxbs+2maMpnW0ML4f7GwpDOdJtVuKcz4+v80TfjpNDF+OSPU+KC/tDB+DxvUePsftDD+HC/U'
        'eL0PtDD+phjUeIVntDD+oSzUeNFbtDD+hSrUeJ7TtDD+nR7UeNadtDD+iwWssbkUIvBsU0eig6YIFzOXlKZV8pQjXyky/psTLF/1'
        'A3mvl8TLojcsuCI2/CTb+O+uMGPK/3+c5cKMvVEk2kdmrvjhDjMi4UQcF552nOwicoz/6QXTz2tBqKUY8IZ0431IaEgzyF0nNXBI'
        'Mh7YgjxDkvFfXXG0yF2L5h2/9ern50cXNlXac5Db2fhkh8VsJtumE6jl85sOOYS0PvFNrvfmmKbrd2Prkf6uR3TOmog1/pUhuuE8'
        '2ZdxKT4D7++wE9klvq+GZrr8hI94lD9RhqkrI8r4i5KopqvP/02ggoDVP4K/iTD+ID+q2xcQrnJnwBTvJaINi5zmGASIKlv+qEOV'
        'lxBUOoEJyXTGJCLbybDmixshh2QiO16P6P7/PagOaQLDGO5IprM1kNBFgTYBWPAq+qCN3PW/TEpxYgnV9Q2vqixEyOeRVmfAPI6/'
        'gfQ3AJaQt5BY2tpDnUUgLX+zAiszJLn/A21l8BFS91YDqCr2O8mDcdAcrEpQ5QNJxhbSq9LL0HANJaz0PDndny6o+W00GqzUgJp/'
        'mDbGr4ZlX5qMSfz/CqthCmjj/AhYE5gm3fr/NbBMn0YL0//mwuuKJzQw/pEDvMxaSX3Tf04QgBufSnXTv89VRpTnQb+pbPqFMmGk'
        '6sL7JzXN/ryjVAWllVH8h1pGfzo1K9McbcBeYDl5Zh248urr3z8/vnx4de/ikW0LuoqjLESxtHvDn5Pp1lhiGv6Wu8kOixtMBE13'
        '2E2NkgUHkCy7QVlkXmHEbbha4V7KE00wVqMZNZNm3aFOJkLzAKOUU/bUfe+pl5NCBSXk1XTM3SMyK3uWHb5Pk1HtUTAKRgEZAABe'
        'bF3k'
    ),
    'は': (
        'eNrtmXtsU1UYwO9taRvasuqyTXQVEBYFoerI3NzCwxAyYW7R+QhEF5lmTofTbInGzIgMIgoLMtiCj6ESzFBQJBMHCWKcM4iZuokM'
        'IqCM55YxymvdKHTtrlu/r72P3sd3/uC/fn/dnvv7fnc7Pfec75wKQjziEY94UGPoJvvXuos+6yKR/RzEJSZ//mjKxOJtxuR19J9n'
        '0YdugaRcY3QY/d0s/j8xaTWBtQB6iqn70d9GYB2A/sfc/RznChLYW4E9yqAfxu4voMDjge1k8Hdi99RS4AnA/sXg/wT9HRQ4Ddg/'
        'GPxLIMVJ6X5hOsAHGPx3Q8p8EpwO8C90vRe7p5pEZwH8E93fjP59JHoOwHvp/mrIMPtI9Hygd9P9BZBxP43OA/o7cfI6trVy1gKd'
        'jDsgo4TmLwR6x+h7efqHupJM++jHadoJvdj9H8tXnOYf97cfOdHtvdw/4A9IRu4ioBeXF3jsXDSStf27EWmXtd7g5MHzJvMY61hH'
        'goVTC1NI0/8uELaA6jxMDa+mfzEAmerzJDW051MPAKWK5jvZ/Ps1SwcrAPWK9mlkdcrsl+pa/Vr+f5D6WdGeaehNmJ5Xtuabjn79'
        '8bwD6YuK9vVLnsjNnjFpfJLLYTONvN0WuyvZnebJTAV86eGrtPdlFfCp1Lf9TeBrqPwL5NIEYhnw71F5nA9fofIrgV9J5bE/11P5'
        '1cC/Q8T9PON8uw74KiIeGZ7Hqf564N8g4vtwcSFX6FhsVBLxLYzDU9gMCa8S8fcBzyL7GyHhZSJeDviTZP/XTKud8BTgr5H9TZBQ'
        'TMTnAb6G7MflroiIYzn2KdmPA24REb8L8J1kfyvbF4bLYAvZfwASHiPiZsD/Zt2rPUrcWOP0cJLsP8Q0n19j3m7ihDWPRl9B/wWy'
        '/wQkzKHRfeiHxdT/zCHDjLOQkM3mvxb+VMOZy40OFrBcfZDmv4r+wXBnJY5cJTWEdDMuQUI6zX9d2j9vhy/v1fcPQIKH+HXxYnnq'
        'HRe+/FY/AQvrqUS/DfAz0comQ7m5b9tYmp2WYrO4psx+btXOLjxASSP6EwA/JggXHCobt4MVbkVViGcJk4j+26KbC/jzc6Q3jz7N'
        'axWfbqL/nsh21usMX+wRbwWXm7WLW0djP8k/C/AmHDwzxTuDefrl89iiVoL/cYA3X3EpBs/QQuPK/74tN4z8LwL5AezCJotjfwVp'
        'b+Gu9+v73wKuIklRhZ6zRRQZVU0H+wZCAV/3kZav1lUU2uRPuP3DgJ6/Fih4tRLE76wK03M6NApiMaZsMy6XYor0qdCSHxQM/Rz3'
        'kPZpUIuUE7fYPhj4lh7Ngn6C7AnP9mj4T0qgB8Tm45zqZCHxn9sk+0fGrVWvkINjRKY25sjvYZWM5Mh+0F/jkj7B86vqAyaKRwxn'
        'xNZTeCIa+5L6TeKKdP556fzBV6j550bvS2voIYtWmfk93LDDpzbpNlm1yCyO3pZtCTOwsbBXjgdmyuf/0AZnJH/GkM7p2Ej8ptrs'
        'XC6tXfoik5JYf57GJl69/7+IimSP77aK/ZqzbHtnjy/o69pVFv1G6yTs1kS1AxKM3yMZc+XtVbrzDn9W9scs4LgUjcJjIDIEyhQr'
        'f46eX1nefuRo1HqDIwN0g6L9Ypa23nY4puwyOnDkvowZ6eWaq2MDw+nz65izJ/ZWe57qE+yfs/x4gRta9ROCf1ekm5Tr4lKm316i'
        'A2i7xv3Le2tKcj2pTitvTZz8SOWuQYEtfPml1Q3NHb3DQjziEY943Oz4H7NXqZ8='
    ),
    'に': (
        'eNrt2FtIFGEUAOBZL61uibFWVlrqgwZlWnSRwEv3wMtDIoVpJdSDZUGWmAXSPdJ6ULGi8pqpZEpWUGGK2U00K7FUXLyUZpmmmOb9'
        'sumcf9dZZ2dncPY8tedlZ/ac/XT/2f+fM79SaQhDGMIQ/000uoU/aEX0k6mJsAtM/Ivk76PoMB9E8u3A34o1/MBTl5D8VOKXIvkH'
        'gLcYQfKXg+8ttH60s+FjaUnhs8d52XeTy3nLuyXgX9ZptpTmxkUE+7o7286hmBHG6z8nlSVas13vUiL919sYUxwRwOufgULT/umJ'
        'qrhQL2uKJzx5/R1Q6MZKnKMExDJefx4UHmclUoT4c/n4b6Qwh5UpFOJTfIvKQ1LXyMrU8cgmUjNzmew7jx8NxVbsTL/mQDi5+x86'
        'FXs7p+DtJ0VrV+/QuLDZ4gsf364lZTXxvmSRR8iFtKK6/pnO3qXgn9aSCo/K+NAncnHoJbM3D2nxKSPDq0DyyY/cfAzJPwH+Gqx7'
        'ux/4+7F8svhfReLHzcHPR/Jbyc/nC5L/CnjJAJKfCb4t1uWNBd8Lyz8GfjCWvxv8k1i+J/jxyNMrF8tfDP4bLJ9M32okfphM3x9I'
        '/m/iY01fsvxIsYa/CXxLLF8B/nwsvxp8Gyy/FnwHLL9ewx/W+xMYaZ6XwFnMujo9+z/BX0ifdFhSspusG/SgHuaXBX1yePLQp02V'
        'q8i4eHCbk5mozuIP+EaTxzUm9HGqKrcSctfF+KoWf3LnxIc+Wq1uFB0hlS6q/TECpEX9NFQ8vXHPEXWBLQGpVI6tog92TqUcIJUh'
        'yif/ZCHZ5DCuZd3aRI2/0gWQzD4b+jWEkVoLqShRvgcg187TL7O+sjtrP32053st6JdQZuooWVtFPXkEk/4TRr+BmUogv92XYvwj'
        'zEfcPRqpcvKuvxj/LNOv1EiNyMh3KxLh32Lw7tNyu8j7C0Q8Wz5i+JlcOfnM53DZFG81NH1PzEmdc4lvmpnfPOWHspJZzItjHxCd'
        '/qKqpXtwVDk+OjzQ09nWrKh6/7ogPyspU0cDJ1EDWvbffATtAFFyHV9Ariqy1rLf0m4v7A/0cPsrVDVBWvsvO0H+Z97dPcZ9SyN+'
        'eQvxn3D7oaqaGo6CewLGKJHbv0JKpJy9z3D2RiMeP4Lbvy9kH7PtTqDOC6FjE1e1im3gmyntxTcigza52stlphKJiXS23MbR1cM3'
        'OCw6IbuwnvtjHRT3/p5egmyHb8HyncHfjOUnxaQ9rWgZUhrCEIYwBHb8AwitJ5A='
    ),
    'ほ': (
        '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'
    ),
    'へ': (
        'eNr7/38UjIJRMApGwSgYBaNgFIwCksDHTn1u5YwbtDL+kDwDCDBX/6KJ8WvZGKDA6i0NjF/HwgAHGg+pbvx+dgYkIHeHysaf52NA'
        'AVLXqWr8HXEGNCB2iYrGP5RnwADCZ6lm/DMVBiyA/ziVjH+tBTdy5vv9FnALeHZTxfg3+jADZa4Aub+L4BawLaOG6/Vgxonfgoh0'
        'wS1g7KPY+Fe6MMP4LsDEpjPCbSj5R5nxL3RgJrHvR4guZIZbEENRYfRAFWYO0xpk8VWscAuc35Fv/A1ZuDEzUGU2I4oLtZvkGn9O'
        'FG5IB7rcHh64nOAe8ozfxQs3ohJT9owYXJZlGjnGL0KEcTbWMkkZkZdzfpNsfBsiEaZiT4QvjREW2D4jzfSfCQi9ibjS+Gc3hCLx'
        'fSRlWluEzti/OJX9TkcoY24nPqtdVkJy/V98KvuYECp93xNp/EpuhKZ0Aq7ajEhkDPKHiDH9TylSIZ9H0NOX5BCqmaoIlxbPHJGM'
        'ryHCPS/tkTQYE2p97URkG2KL39/5SBZwzcCrdiIi1TOwLCA2PSzlQrLB9R4elacQrSie7cSn5wuKyF7o+YNbZT28AXKalAzzzh+5'
        '6je5gDswzSBKdB6QWJ70syFZwFL+GZe6m+Cw9PhIcoF1WgmlgbcQV8qeApQt+ENGgfshBKV9ZH4Chzp3joVkVkhzeJAtYIx9jFXV'
        '09P/yQV3rFC8wFHwgsrt7D8trCg2cJW9obINZzRRm6m8k6hswY9KFmTzmQ9Qva9zzgDJ/DYa9NV+N8Mzm+e//7QAV6EVrOyb/7QB'
        '/+aDGmcsx2jX23+XzsTQQ9PxhJMlo2Mqo2AUjIJRMApGwSgYBUMWAADUbSxp'
    ),
    'と': (
        'eNr7/38UjIJRMKDgF+2Mfru7I1TJmkaGfw1WYAAB9p80skCeAQJO0Mj8OKj5/TQyfz7U/HAamf8Aar7cfxpHwFMamZ8ANX8Vjcxf'
        'ADU/lUbmP4Gar0CrCDCEWnCLRubXQc2fQiPzz0DN96OR+f+kIObz/qaRBWlQDxyikfmboeaX0cj871wQ81VolUKDoR64SCPz10DN'
        'r6NVAPFBzNemVQDFQz1wk0bm74Sa34ZTxZ9jNX5/yDb/jxjEfEPs0u+XRwkBZZeT74EcqAcuY6ngJjiyQCT1yTf/GNT8YjTxa02w'
        '0hUEtpJvgTrEBHHkMuhavRYDCrAh3/weqBGbYQKPOvQZMMBhss1/zQ4xIRjM+zzfkZEBC/Ak3wMREBPY3v7/fzSJB5vhTAYFG8k3'
        'fx8sC0zVwWa4Tt76d5TlMVUGXEA4fB4VGkdd2A03rD/1lyplxCtWDLPZ3Kc+plYRdMoN3XCvhR+pVsDdDEBLK67z3lOxh5TLgmK6'
        'WstjKhbO/+aLIBvOHnOUqmX/HTtk02VbXlG3apnCjWS67rI/1DX9rR+S6eYb/1G5XjwnhzBdcwP12yVccNPFZ/6huvHTmGCmM6a8'
        'p7rpSAWOKi1attPgxkd8ooHxm2CBwzqDFi2q67xQ47m302S4B9bsEDhFkwZhI6yw2U8T4x9yQs1fStseVwxtjH8BbeyIv6ON+X1Q'
        '53fRqLlvATGe/xNtjP/EQtshq0PQ4JlHI/Pn0HjIsANq/n0amV8PNf8Jjczvhpp/ikbmz6XxkOpeqPl6/2hj/mdmqAVzaOQBU6j5'
        'XDRKodNhNS/PRtoEkAC8bo9/TAsLZiA1l5OIDqR/j/dtJlKlM3KbWT57Ff7+24crO2aXB+kCKz0pIp3yAb1zLuqY1rZ457m7L7/8'
        '/PP/1+e3zx/eOrNrxbTW4ngXDeTeMLE10gszBrIA0S29n9lkmT+VhDEZCzLMzyQlGW3xYSbVfFvSEurjPgdWkswXJDkvfN3TFKjE'
        'RNBgNjnL4PyelcfJKxN/XN00rTrJ305XSYKfg5WJgZGFg09MTtPUKTCxqGX66gPXadRSGgWjYBQMbwAANpCtXA=='
    ),
    'ち': (
        'eNr7/38UjIJRgBu8SO1ad/kb7czfzQAEjDIOn2hk/mQGMBCmlfuzIeY70cp8e4j5RbQyXwRi/iIaGf8cYjzDZRqZvwNiPMdvGpnf'
        'ATHflFbBHwExP51W5qtDzJ9FI+M/MULMP0cj8w9AjGf/hVPF061bKTC/F2K+GTa575cWl7iIMjDYU2B+FMT8bBTBr9c39WY4y0GD'
        'jkGOKJMuX3r8FWf0Lvz/7+ODC/vXTatOcNMWYEAFTL+IMd8bFMwiCjrmTl4B4bHJaRlZOXn5+VA3SgozM+AGd4gxP5SBbLCbGPPj'
        'yTd/JjHmZ5FnNqOcR+FJYswvIdVkUdPQykVnvhCbEuuJM5VF0sA9rmL6tmukVvnr3Kz01eXEBXnYMZKKhHtoSlFj/9w1+66++UeN'
        'UuHvr5/fvn75/NkNYn43jQqfH1wQ80/TyPxdEOP5/tDI/CKI+T60qlu0IOb30Mj4R9Dkc4FG5s+Cps5/NDI/EGJ+HI2M/wZNnUtp'
        'ZP56aAH2mkbmx0HMN6GR8b8FIeY30LLfAgRnaWR+JsR4aRoZ/08KYn4GusTr7d05PnpywpzMAnK6jnlzz/wiy/yD0ODZhtKXnB+h'
        'iFHjCCbvJqMATINo5v4BF3nSZsKIo1KTW/CXRON/CUF0hsBS00oPJnzVps4O0szfCNW3HNIgnChHsGKOeUuK+WHQdjOoV/2tVZiY'
        'ml+BhC7aJ06IHl9gXTxfmsgGC+9Ros1fANWy4P9xPfTGREDtsmP3P/z8/f7R9Z39acjSgleINd8V2th5mIsSq+y+0+6iK73TKQuX'
        '1/xOnPEPoKaKI0cro+OCj1hVf2/ngakpI878WszA5Sm4iVv9GVGoKs4XRDWvZDHyaOt7vDpu8kIVVhNj/lY007mrPhDSMgWqVIWE'
        'ihcKmLOJ8PQ/Tajqa4TVvmRFNt7uElFRVgdVvoSw0k7k1L6EyBR9DqqhgrDSG4Zw48OJLlQ+QHUkEaH2ZzGkIBZaTkKJBW3NBBPX'
        'cpYEKjV/TEqJKIpaoBMAr30ZUn+SVJ9Cez0pxGo4QVqF8QAa/qU0ag/AStx5NDLfB2r+JdoYfx1a9QvQpi/1zwXq/FTaOL8XliEP'
        '0cT4xbCGkQ1tXA/v8R+jgemvECNHNAj939MF4cbrfae26d+myCPKcuk7VE6UR1P5kKoK6VvUNPz9+hRJlGpU/x61jP5+dn6GLnpr'
        'OoHyKaY/z89vnpjvp45lqFJhI7mGruhtrS3Nivax0RHH2QHgrSXf8e0Em83CTe8pCJRPgviHJ50WUxjwdXjGEu17HlAcq295sJrN'
        'pJ+58j1VkmMputEcBjGduz5SLSe94IC2Q0W1HaOrZ+++Q+0qateKLQfO3Hz19/8oGAWjYFABAJQTP+o='
    ),
    'り': (
        'eNrtmFtIFFEYx2e1XHalDdEKTRRvaWWF0cUCLynkWkj1oNntQUqIKHywC4hFIfZiGSGEGERoWlqEJBhGQipFYCCZmb1kobmSCwqW'
        'qes6qeebvYyze843zHmJ/Z48M7//j/XMzDnfjCj6yle+8hWPahRIbcMGa2raBqboWDv4w7H+9YuptTuPUrCP4Dci9dM6koukcD/B'
        'L0zj/AMQy6BwM5J/BOd/CbEzNNAEYB/OXw2xmzQwGsBOnL8EYg00cDeALTj/SYi9o4FZAD7G+dNYL1segDU4fxxJBczTwELwV+L8'
        'RpKKooIXwX8DpZ+AVAqVLAPyEsr/FVLHqGQVkGdR/k5IFVPJWiBPofzPIFVBJZuBzFP1+D6ikm1AHkL5yyH1inkmzSh/MaR6qGQ3'
        'kOko/2lIDVHJz0DuQflzIUXfIb8BuR3lN8PyQCct4N+M8qeQ0Br2Jz0B5d9BQtF0chL8cSj/FhJKpJN/wR+D8ieQ0C46OSuwrrQK'
        'yz99+RTnwR+B8seyPzQ6NQ3cBhLKdB750ewBXUHQMDXzv8955ILew1oUQNBQlH8rCaU6DowZBWOXIuqnZv6hq9nrOFC6MDJ98HJ9'
        'o1H+DFlXbwlcHAb3eW5A41H+IyQUK+tCQq3LyCmB+VF0qQISCobhJ3/4f5a3Q1bwJ6H8l0lIZ1sa2b00mYOC/FZgqTuQ+r40ugWj'
        'NAWyF87lqOoflu75XgMZ+HUrkG+BPIHy90CqfOHv8Rhv7xotcPIcyv9b59hVbdlgCBlTIu/D2RJcfyv95vqhg9IbVp3X/rMK588X'
        '5HVYGTwPp5/j/Pfk+rBfymAOnH+P8w/K9Cs9vb9tZO6UFHd4gfL6Y9eT8/o57PcHN/11WnuViNSLMxEsevEpELnoLy+tOslueOiZ'
        'ugLMVfynnbuwM6V/8QJlgr9JxbejjixTQEzBG6/dlUHl7cNYbWo/LzFWkaq3L+ayrQN/NR//C+kWG+bjz1T18sJcXdLPL+OityeD'
        '3p/P3XlbULW3M39ilJ4toZWHfiJe0ifz0M+ZHatrOw/9cYHn7NvyHPqgEe3149nOvadOe31/nFNfqL3+wSqnPm1Wa/uwy9wI8WMa'
        '26crVrvoEyza2ucbo1y7lk2j2t6UtYluTdH+cS3t1soI95auaE7DiXmdr3e3BzdpOjVmWb97QOMrO5nkag9v0PyhGol02A3X/nBY'
        '0vqDiD2w2CJyqY7FKxxSahV51RO/1PoZkWONir7y1X9W/wB44pCC'
    ),
    'ぬ': (
        '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'
        'id4zHs+vGeTtZCc72ekOpH8BkGiwTw=='
    ),
    'る': (
        'eNr7/38UjIJRMApGASp4d2HTauqb+u/Vha2z6pLctXgYgGAxdQz99vj87qX9lcm+JtIsDMiA6wp5Bp7aumbJ7P6msqxob2sdGU4G'
        'nED9E1nmhzEQC0LJMr+faPMZJpBj/knizZ9Kjvm/OPAZySNOofP//7fBajCXln/prANPXyrBRbrJTJGlyMYySRj65fasOf0Kmlot'
        '4DJt5Kb4dYz8SqbuUblNszafefoHNYcFw41vIjtH/f2NU6oYbnwtLYqdaXDjK2lh/HpmmPEltDD+EDzh5tPC+MsCMOOzaGH8Q2mY'
        '8Wn/aGD8Ww2Y8Ym0MP6zGcz4uL80MP67I8z4SFoY/9sHXuT/oYHxfyNhxgf+pkXSSYcZ7/uLFsYXwoz3/EkL48thxrv9oIXxNTDj'
        'nb7RwvhmmPH2X2lhfAfMeOvPtDC+E2a8xSdaGN8GM970Ay2Mb4IZb/gOxH1/YHZZoLGqBDczj4SyZVzTyqeUGV8PM17v7f9HizN0'
        'mTDaLKppu/9SnjB19jUZ4WxvyVa/pLQVxI+/kcjbQkbG+JdJfCuUQekEqcb/iWMgBbB0kWb8z2AGEkERSV0iT2xGCLkUTdt54dmn'
        'X7/fP7q6qStRAUW2kHjjP9pjxmHojJuYDZZmSSQlS4k1/oUBmuGcUVtxlMw/piNs4L5DnPH3VFBN15z2EV9HGF51MoQRZfwlSRTT'
        'rbYTXYgwniHC+COCyKab7CElI6YTVrsJubsru4yoltofWNNLiGDtP5MZYTpr6RciE8QJmJaDBBTWIjle/zzpHcI+/I20RKReXQ0p'
        'LZ0JUF0x+BR98UAYL32ApOLkDFSbCz5FSxDGO78mcYgFltzwqqqDGV9KchsW2nHSwK8qF6yIbRHptQUvxHxjAnVKLCgRoyayT5ty'
        'LZR4OWSNEhY+xqnxByxcCTXz/RhkrqF0iqp4ELHC6LMXh75HsK4NIX/+SH2EzF3Fh1aS2t/Cqm01UekfI7gqMOsYzh5sKmF1NWmp'
        'ugxrRZiI2YP5Lk5s+YMMluOoad0w2tEzoDJJJHVIofUAd86uB18/31keDx8c8EXLHh9koRJHSTF/KkSPMrzafZHDCDUnA1VlNJGp'
        'ExU4QTQhJ8m9YlCTpiArnAhLviQ5/z9kMEMJRewmNCQ4r2IZEsolyXholrREFb0jBBE2gHUk/1TAAk31C0nm/4LoEkVrfu+DDkE3'
        'QMdU7GCuF7xJYpEFdela7IUsJyiff2/ighnPcYDUItEFolHkBpq/9CDi4f+/TJZHNO/2k1zkToI1y3pRe3ZHoOIpQogsJ36K9CL9'
        'PXw0jCdows6b777D2ir+GDna9hk5/ZdJGI18LlElE4949KY7azV5Yyr/gohq+NteJbd/98OfsOkG6ykZVmpgxWs4k8cmCof6LgUz'
        '4TTdqOkhFbrwNwqUsZitljD7CdVGCa7PLvLUkOZjZuEWktZziaqYd/Td/1EwCkbBcAcAXduA4A=='
    ),
    'を': (
        'eNr7/38UjIKhDw6d+UdT840ZxGKXvqaZ8c8YGYCAyWwjjcyfzQABu2hkvjvEeOm/tDH+NQvE/AoaOX8GNHhu0Mh8O4jx1jQy/g4j'
        'xPxFNDK/CmK80HfaGP9XBmJ+GY2cvxFiPPND9Ey3qc57JhXMd4OYH44Q+XhyfpmPFEgwlXLjb0Fj9+z/708v75xVE2sHDS8QcCTR'
        'sGJVc7fg+Kzi6qbOvknTZs6eN39+MMQkJhkuBkwgS6L5iQykAUYS01QcieYzXCHN/CgSna94nDTzw4g2WcYhZ+axT6QmlmBC5nIp'
        '24QWTtp6/Qd5iXF6TICrtbG2iry0mLAAPy8PK8RUkbi8+v75Gw5e/0jdrPtHE2L+LBoVDfMgxqv/po3xP+Uh5q+lkfMnQIw3p5Hx'
        'X8Qg5h+gkfnNEOM9aWT8W35IwXaRRuaXQpwfTyPj73FAat1XNDLfF+L8eeAWxMQgdT4WMe2Y+W+pZfw2iPEO////W2OHKH94St5R'
        'J2upgo1jv/F/ty5qCSd3nBrmt0IMa3wbgVGEsu3EneSIrc4eQSpbze1SWMpontPYs+MyH9ZmIs0PgVQigcxYKwFVTGf+3hzJDbL6'
        'OVHG7yFQy5Sjqb9QCC1KGJKJMf6bKqpxHGGLrn/5fmuaGkyA/RmS6lcTDBBKicrthSimC7fAkuTvUgwP/Nsdxoai2pmw8UeZkNQz'
        '579HkqqHigpA6t/nbUoYYbeZkPHf1ZFUa5xEkfsHbYwygHqSh8JY0A1ndZn0hGAbEUl9/Ff0vga0xo/9OksP3XCBiOUfCIfOcUTo'
        'sEzG2cLjEkAzXDhl5y9i0s53DbgWXmzd3bPY0qtIyq7fpJX6QCB+DqsCdXTDuWN3Et++2AsPHZmbWBXsVkMxnMl10RcSirVXkvCW'
        '/V1s8lvNUQuKzqcklZr/PGA6pW5jkd5vhVKOhu8lddCmC54armJKXvVCcXs96cM1J1lhUXYSQ+5dFlpZSno180ERlu63YcgtEEVP'
        'NqtJNj8UphWjufzQHSnFiEDoyaQafwaWNDE66kv5EMYHXYaWrvUku38fpDL0RRtF+p6AMN3h1P//dRBmHumV+itQ8tT+jFYVG8FN'
        'V9mAlMjIadf962QRQEv4l6ThtVgTpNCfBA0ostolx3ejRYogzHibm6iDfe7UaAZdhRnP2gGPlQVQ+6hg/CsFWHFxDCG4BCJkTLnx'
        '/zyhxhsgF2MrIWLalJs/F2q8OUq1txqamCg2/iu00aSG2mReA23nUmz+FGg5fAlVeB0Di7iuc0QNxeZD65ISdG+9oc5Y/UfowNhD'
        'GnWQjkGMl6eR8f+3QAcfaGU+tJHO/plG5l+Gpv4eGpn/hxtabtJq/CEQ1pvo/0MT83fAKxa1mR9pYYErUhfJa+KZX9Q2/7E4audB'
        'J6i4f/nes3dffPz559+/Hx9fP71/89KZwxvn99VkRbiZkD7YfVqYlNFWMsa5rmuRYL4yGUH0vYydaPP5yIqEB5ncxFrwk7xo/jDL'
        'lYUo85+SnZI+bil1ECBo/gXKkuujfbNrUvwsteWEuNiYGJhYObgFpZR1TB38YrIqu+asP3T15/9RMApGAf0AABUBesM='
    ),
    'わ': (
        'eNrtmGlMFFkQgHsARblkPZAYdJVdVxM8F+O9EFGMBKJZNQbMaoyiglckYrwi8QhBw8YjGFcwUXcRsyJoPEDwYr2PiBcSIxhABVdN'
        'hEFFrmGewqvX0910v37dY//RqV/96lV90/O6urqqEHKIQxzyLUrDrKg/5i1YtOSDQfx3HJZag/hVwG80iF+G8U5GnX8x5rsbxb+L'
        '+T2M4l/D/L5G8fMxP8Ao/gnMH2kUPwPzQ4zi78f8CKP4uzF/plH87Zg/xyj+ZsxfaBR/LeYvN4q/EvPjDcL/Px3zu5z62uSWp/+u'
        'neLLETFtbPla5KbSvD2xY9w5iYRV25+MjyfHhPo7c/Li/9BefghHFc+3dvK30fl7xdbmnOTo3/r7ujl7+P46PSGnRp1/j4pfKbC0'
        'Xo4PdBJvu0xKq1PhW3uIXXyiDoznF+G2AHoY7yd7Bz+sM9N/IEpgO3XHfStCk8h60HtyE6cnKP/HbnutNP5BbNU7MuU+3G0QcSwD'
        '+uEB9Ic0sZLCr+Q6j407+kKgGY29nC/hZd4wTk18b1F+4FGTRBGInRLaFiWhHIN0OqshZIdinzOt73ViJxHHPWxNemFFjaXu9e30'
        'pT8Lf+AcOz8Au1xA6E6AEO63+qK4ZCyc48JvehQz8+FpFli3dxCE+sxcmWxXHMwb9Dez8n/CDhkTbXTXxWUKKXeTidgsZuX3hTvm'
        '6c6xVcrWB8g7bbrOyJe+qCGPqOY7iV0QI7+7iN4zS80+kpheYeN7CvFR71Ttq7uBbSQbXxDy3U+wOOwCaze2js2Vx497yeTw0Rvs'
        's5naLxI4pvhmxicWAx4rGGxLITy5LrnMb+RZcAlUN33QkxzODfaMUgsvmYd6b+TNH/5LDSmRpLo3Kna5bpwuPvnkqZQzWYKEpok/'
        'G3zoKeKIC6eTvwB8ztOM/hZXH1r4c8HnMsUmzcTp5v8OPoXKJnsIPhiK0Rca+GPAt0TRIoXc9qhauHiugQ9RbapXMviL4AdXN8JV'
        'BTu+Alx6K7a85HD8X6FPcFnOzt8HLpOVajiC9ylF6D1cl7Hzw8Bli/x2OglMz9bnT87/GftAyokanpmke+nQViTVwKqUmb8CPLrK'
        'TtRySFIwpSMh/ykrvoQAYuV2/+tMQicJki0snzDirXw9f1Nm9w7/NY8BzQdYsxZ8SbT6pKgr36tYQFUPiiI2/Ck+KV6Q6dX5bjrw'
        'I9E1s+VykAK+2giXO7v5sNnnlU0Jqnss+Ey+2PCQzSct+MvgJTwMCNe7DAOFzbacmyZvYpnRWsrmC1UdscNtVfzzIFs2j1YcPURw'
        '3D6Rxp2tfmhMEswrghoU7RpCV4kVXtjlKpVu+UfYHo2lVYb1ksYEytUCiktd6i/CT12wplG+j2I4kxoszlv0JZ3fpGnk0Qt75cnv'
        'Pk4MkPSmKRpHKj9iv9Mylf7JZf2kre/wYqSPLy79LUWHlg41tWusvf5sQjr5mepzKJfo1zpGTsDPECnLvdvROy4sR0g//6BYmy2h'
        '90qo1DkyA36qRB0rgLtOy25GesUfM6RhVz+EtHERh8zIDhmIMclS/ZMv+cZpSFx+g50jRbjPre02jq3PNSP7ZQTmb0AOcYhDvkv5'
        'DFZlqbs='
    ),
    'か': (
        'eNrtmWlMVFcUxy/D4qBBHVFsDMUFEFfc6pJq3RolkQSMGAlYaKDF2hrTQqNVE9toXCPxg3sVYjAQUUlG4xZ3LSZqGqNttRi0VtSG'
        'yKhAyeh0mEXgnkPecu97by6+L82cj+d/zu+9e+fe88694/cHLWhB+x+bY8vZejP5lwghMXNXlP9jEn8voXbIJH4h8P80iZ9K8T28'
        'JvHjKX+aSfiWEMr/1iT+dZj+cpP4u4H/0CT+EoqPNmt7TaT8eSbh34ZT/jqTf94L/JD6whZxfjHFh/IR5X3IHLcwfyHlT+C+fFq7'
        'nO0T5cdq7662l++wIkH8XzD9VWz5XgToZJ8Yv5Rmh7zk6JVQPUjYJSF+Ds0eww3YjgOw1Yrw42hyIT+iCB+Q9K/49C/InBJvs1oi'
        '+0/K/VnxnfSm4gMyAiF7666VrcsbT9RmSflFFtk8DJViQwXhnr34m5TECKJhOU3SjNre+Btf0wK7H5zYVjAzNoQYsJFPpZmnMWfA'
        'Sy79v6QwEoANk5FWojud//pjSECWJs1t/Rjde7n8ZYHxyVFpch2UCRLJ7WIO81EHHE0tLrfX53psz7SgM17Wr1Sh+yMPh/9cSowY'
        'mbGm9Op+KA4NkrDqOIw5LkvPQvdm3gAGUn1g+tqjNa0dnjx4JVlYjQ1A82Xu1wPA3Y03Q4tDR+Rsv9IoWbAwqT/J48pwqp0y9xkc'
        'wGROo/fCqXCchYTbip2N+/Wy3J+LD9hpsERAQpzSvxE4G+XuBlxDvYwdF5xRNPw7pfAHcLIV/hIcQJYhfiVEX1cpUazf3e/3TcMH'
        'XDTCT6OxH6o/3ROg2ij9v4cCf4RHH98IFfR7tZRJlXCV8BUOYI8+v4S9etqtACSXagn2BKVfky5/NhRKjRNTs0rZigNYoYd/DoVm'
        'A0NbA5BXKsU1GHdxnQ5/E9SeJwwNyz3jg34QB/CFDn8oDZul1TK8VUse3Nxh2u1KNYSVscTlIHo0do3OJsunQVFOlrgU5o6l+ZKB'
        '36NR69AIW/RLdqWFhzNF+NIMuWPkTuCW1s6OZTdOSe1a6mvN6YFBjmars6g6itsSW9b7DB2KdrDlsVSdyumjYqPP6SzObAqwcgb5'
        'AaNFkZj9iQ7+BZS2XE5rCmXya9Ez0Y8wPTc5jyf80mHE3vSl+eM5+g3gH+zihVUJR68AXexE5PdC6bE5OQHrgS94IWeH9B94AYuo'
        '3lNweuDMEvaMF5BIAyaK4U/C62dya5PFWIXXvo/hLU6//xwE7BfCYxs5nRuxGiJ+E8H78PVPcUOmQH33iPDx8zOKWwDrYfpTRfDu'
        'BN3L4D0Bdsky24WHH/7gp3bhPrE5BpJL0XNM1WNCxPCuXGUPakWPZfpt5oWKUPG8j0fsA52ltK1LyHogPapDRMjfAvxPITmh8/Ub'
        'Oi40Mq7gajqPDewMAfwRbI0qOl2PccKKTj1zO6o/77xDsAeOf9Ufckd7Vb+m0hIE/gzAX46cUX+qVCf6wPF4GiVzJM4LbHyyQG3Y'
        'Al2BRVq37thYeOtdkcX/K72hKZA5HUvV10KWCrHK37rZ2vbVdSi8j/IVV2XdDwtfNj+czTr51W8aLsGn1HTlOv4Ye+HV7vxsXHR4'
        'RMwnq+76gxa0oAXtvdg7qI36uQ=='
    ),
    'よ': (
        'eNr7/38UjIJRMALAdwYI2D5q/qj5o+aPmj9q/qj5MPD8Me3Mf70mW5MhhSbmfz7UF63BCBIV/0td85/un1nsq8bEAAdHqWV+dFag'
        'hTwbAzooo5b5OIA6jc1nuEFb85nn0sx8Vr2Eyce+0SJ8BE1i29bf+E3V/AsEHAqWQfn9Gy58pEX50HX13Wj5OWr+qPmj5o+aP2r+'
        'qPmj5mMFD1c3RFurCXExswqqO6b17nlCRfN/b02TxdVao9z8p+VieBqaayk0/U0uG96GLKNh1bF/5Bs/T4CBMJApOE6e6V9DGYgE'
        'Gp3PSTf+ixUD8YAl+ACJxv/zQdLOpJ8/c/ftl1/+/nh/9+Tq3gwHLFbozv5BivnTETqNp78lqn8h2Ul8I/2bOEyX9VHc+ZcDzQb+'
        '2vdEmr8MpqXhH57yYdNqNyY0G+qIsyEWqr6AUPnzoFYc1QaBDmK6X3rQAPiA1/wdIM7PBQaoNkjP/kPQfGlo1OLIGlCT9kD5+3wZ'
        'UWzQIlgwyeDtiT+CmnMELnLeD9UPHtfwm28G7c6+wip7AmrKJSSxM14oFnDhz9K5UGXVWGW7oLJvUESPmyOZ34Xf/YdgGR9rvjeG'
        'SHKj5/kFkjDjXQgVq5Ywf27GlFsJlbPEkPlcwQ6WEXlGKIJPs8CckvYJTeoMD57Mcc0CnPEI54BeeFBKNSG75mc/J0xiNzZ9f/u4'
        'GLKJycJlSKMijpUbrzz5+uvNjZV5ooh8hGMw427qd6LKiC4m/IV+F6WV+14pfMar/aDU/P8fc5lxGs93iRotq5uROGyQPk2lttuj'
        'KkVM0wVqqTmedLbdSxIpMRmmb/5J9Sbth4u71y2Y1LVo3y3qmz0KRsEooBQAALQL9p0='
    ),
    'た': (
        'eNrt2GtIU1EcAPCzuSlqZluriVR+qLSXJlHk7EOC2AMtI6UsepiQZmA+SAobkWAhWpbKJCMGvTRCQwgdpfWhh5YR9tBIzFeZpZiP'
        '1OZjj3Tnv5jbvdu9uzvf9v+2/7nnx9m555x7ztHrneEMZ1iJe1nln6YI+jsQQsK1cTkThHwJMsRiQnw75lEMIb8c/CJC/inwPxLy'
        'N2F+oY4MrxZify+h5r+A7ikm5F8Cv4WQvxPzUkK81hv7cYT8JuieG4T8q+B/JeRHY36ZSaqzPDXWUbxOjP1jhl9DDcq0MNHMT5Gj'
        'u/9MWU5CmA8yBt+OyZywSOzt5eEmFApc+DyewF28ZI0sMhRRxxB7/wBiER3s/aNs/Hfs/UQ2fh17P4Uh7R6073zZAHv/tA2X77Nh'
        'V3JBTYfWzqF4jp7eU1LV2KPhONSHe/oG/6intHqdTqfVajXTUxNV4H8htDicxHwAqZ2VH/YzCfEfoHteEPJzMC/REvJDsH+U6fOa'
        'SfXYKHO+j4/9Squj7n1VsTwxOnStn9h19uHlzH0l5t1omvRdlR+/aYH5VGHhR+EaUZYlY7U5u32opyJz/48brqGcm558Jt8ipJ/q'
        'zP0yXEFgupQN3I71sr5KMfdjcIVw09xGm6srY3/cA1dQmCaPOM6vwM/zek2TeZagR8C2Qxm5ysonDc0d33r7R1h+jkPnJKtNYMG6'
        'WPndt/12zt2/87ByeU62Gz41QUmljdyOk/ehe7rnpucjF5lcNcJ97dmNfZlZOv/hsEOWtkFX7F8jtHSWQkf/IORvxf5WQnwPLM0l'
        'ZA91Ln2EfH/sRxDin8MUvUXIj4elZZQMP+qJ/YOEmn8TukdFyIdDkVRDhjdu29IINT+R/gw0/YD7ReIwvN0gi5Kf2b6ojbNfCM0v'
        'NMu/jBM65J2vgm2b2RHrME7ncuXroPn7zfKpDrqljAT/sVn+Dlzzcdysf+bBhYm5A592VM/NTwDmgkXJClxwghP/Cza1AssPYzou'
        'mT/Exc9CtLed9VAk58CPienvF3QBMHCb7ffzgfenuj8qhsLVv+3lx6VAXKUsNR5Zgrvt9K8Y98TU71Bh3NqKiift4dXGBiZTl2s3'
        '/988SzNf03wdJltris5SF12Dyjy6+4xWb5P9uVd42vXqpq4htUY3MdLf3qi6W5CxT7bUZXZ0U2/JfRHtiRFCJWR2BzVuZVOF0FP6'
        'Lnzkzsjvpag6YPzz6629o+ZAJj5VB6cjRruqiQKJbf+NZb1OWHmQ37Stg3dJsC2/1rJWprFMwWAktxZsF9HrPMkTiuWlOsJQ6KNm'
        'OFvaKvOSIkNWSrzc+HxXT9GSNSHb41KyFRWvuugmX8vxmdGRpycYAxcDR/XOcIYznGGIf8SEkDg='
    ),
    'れ': (
        'eNrtmHlMVEccx2dXQPGKtSJVrBg1xhuteIFQPFKVYMBaa/DEI6YoGE29bWMNhppgNBhArYolWmpLY9oU23jHGIwnGjXS1CtGoF5E'
        'hCLoCqu7v5l5b/a9N2+GZP5p9/fPMr/3nc8u82bmd7jdfvOb3/6DloW8Fq+KnwH86ar4XwF/vir+KuCnqeKnAX+dKv5C4H+rip8M'
        '/FxV/ATgH1TF/xj4xar4w4B/ThW/D/D/UsXvBvwnqvidvHhnoyp+Wy+/k7L7s4WX318V/g0sf0C+6dPKgtTosODA0IHzC+uaQ68/'
        'vx1hyzQ8rM4Z7UDUPshtkkI/O529cGiANh+ls6/4YXpbxNoclxi54mRO2oSuyGCfv9J9+7Igo+ALW3TByqmDWiMrG1eDZY15HUwF'
        'J+z4YxHP+r8EVdkoC0GMYDA0t1Y3QJQdbCmxu0X+5PF3eiVVUzSPIyrjVOWr2nOfEcdWG361kyG+N233H3TwqVdRGk4dHdc+IPOW'
        'Yley3QINprPbfJJ54d2OvELGnZ96nhfStWmXUaNNq8V7tZ8dPxV+d3xmyWtwnCW8w57RJvr1c/9h5iWCN8SOfzBwyKLvbupO4nHM'
        'm/nub9c8Qu9+zGfeavC3tOM31Ps4imFiaJXbXRdP8MnVFlkecsneQIdpevgiGjNa7jLK1uMNJX3DHcLL7a7CgRiFXTKR4ZVrJ80/'
        'ABMXVA3B+BGVZrJYeNhbmr8fJk4divGJL01lYfA0Vpq/hz3DS8zjcL2juVn2Lga/wUJ1yzII2VmeHp9lpTqCBb9J83N1+Gxb1V1p'
        'fo6Gz7FW4eMbLJ8l7aD47RzVDJBESOMr6XXMLTBGg2aGLL4mguDXcHUfgugbSfzrCQQ/m6trxHnMITl80yyf2Gi5ilhV2qzS1GN7'
        'uEIS5J41d2ui/VwlPl7BUvjiFjp+AVeaD6I+MvhrTHrJLx+3gGi8BL6iG3OxFXLFX4IoRRz/70fsvczfeSkgWi2Mb0z0yd1+4sqn'
        'yPYQ6M7sHQWfRVz5GBDtlgy6CLW+PlGEj7O+nwXxF1tp22a8CL+HYPqPtw6tXVJpRVAkUCOjy2I13QiCj2xwu+NE+G1AdN3H/eS+'
        'mXgOzc7va4kNn+80ry6mJ/ACluN3zzBG4NW58Ix7rPsXhH41aEsC2YgSLbD/G/CMcnZ1QhEK9626H9F3GwOpcJTA+a3DUx4z/1Sc'
        'SVvQFUvwIRVMZP1R5Pc/0juXe11BZYxyBcE7j2IPLkV/EHm/D3SufaRmZoTphP818YyE8QEuH5fkV3V1ECkUnbcZ5VZIVOPeEAc+'
        'DN9z+SEgOqJVDTQ2bfKRFnnuhs5ajj8cZPlcfgSbnjZtpn2VSYaMruR95DiqDSNBt5fLTwLRZJw00XIbDXpuFP/dS7+pIkWuXrwt'
        'ArwH7ExPig+vMO38uIztzzyh+3xYWe3xJC0odRFoamL+Dq6o3LRf0eOOePt2G1/V16ybUy7RHs7iq7KM+IQXMu1nm9Bd7duLc2wQ'
        '7PZh/mYbWTaLDzsm2T7faKfTbRsUuKxGtj2/3k7noldXUMpt+fb/Kntl6eIB7YPCk3bKJemYv1xV/xnzlyrmL3b7zW9++5/aW+DS'
        'myQ='
    ),
    'そ': (
        'eNr7/38UjIJRMAqGJvjwiBam/nt5dtO0qnhndR4G1oNUM/Tttf3LJ1QmehnLsDIggOhDsk388+7+xYPrZneUJvnbaIqxMGAHRt9I'
        'NvhAkIupuiQXA3EgimTzTzCQBBaTav4PNlKMD/xOsgdM8Zso5pCZBuek/iE9ZnNwGMyq4lkw6/Db//8vicCEqslJOYvQDebVDSie'
        'tusuzKlw45mmkJUyb0K1cyvbxVRO33LpPar0WZjxHGvJzEtZNVNWH779CbvsEX6o8YKHaVE67IJlDLmrtDB+JSzxGj6jhfGTmaDG'
        'e3ymhfFVsBSV8psGpv+MhprO2E4Lx7+zgxrPuZoWxt9RhxovfoIWxu8Tghqv+4AWxk+HVTC+tEg4v7JhCaf0Lw2Mf2ELK3EW0yJs'
        'TkpDjZc6hUfV3ydHFjd3kmH8LHao8dbPsfvu+LK2VFcVcMEhRbLp3+JhQZ/5C1Xm04V1vTneWpzINQXjVxKNv6ULC/r58OL78YG5'
        'VRGmIljruEskFpd8UH3KF4Ators7pxT4arLjq5xJqnK+p8O0SXUU+aizEtGcICWCb+gxkAxSiTH4y829C5tSXLlJMlnQwC+tbtoR'
        'fA2r23vmN6V66vCT5mQei+SOdVe+4HXx7tJwCylGkkODzbxw1e1/RIRIBzGm8RoFIqcdlfytRKf2XXgNZlH1Lpp54On/l1aIhn/p'
        'GVJS4hscJgtZJnasvwbNuJcVYMLqi3+SmE9l0QxmUvIqmnX4FbKS47B4l51NeqPWH24yu25Y/YoLmK3uo9CszFT8nYxCshHU3jSK'
        'aVt/E4fbjvFCjFc8RFYZf7576318Ce2+KLSMfkeTnu4nHYjxft9oYvz/YIjxzr9pY/xSaI56SxvjX0DaPiyXaGP8/xSI8wtoZPxd'
        'SNtK5AONzE+AOL+cRsZ/hLYQbtHI/PkQ481pNYrkATG/iEbG/4OWa6toZP41aLl6g0bmL4ea/5pG5k+Gmv+HRuY3QtuftEo+FVD3'
        '06jo/N8KNf8ejcyfBjV/DY3M3wk1P55G5sOaRrw0qlz+qzHQtoCohZrPfIA25t+EtatFLtPGgjB4o3Y/Tcy/De94MVd9pYUFnYh2'
        'r9z0HzSoAryQh4cLz1G/eWiJ0niXTpp/lbrjKB+t0HseXAbh5RPX7D9/7+W7L1Sw60canv7TZmp4YbEwTvOpUzW/ScTVgV1ApVi4'
        'HMGE1fzpVIvnhw0KWMzvo2ZeOF1nwoxmfiuVs8OXvW2Rekhd+WpaFBp/nxxbObEhLy7E1236/1EwCkYBOgAAVJ6REA=='
    ),
    'つ': (
        'eNr7/38UjIJRMApGwSgYBaNg6IIvlzZMqEjyNlWSFORg4hCUMw+sWHqdKiY/2t6TaCHGgA1Ixmz6RYHJfy7Pz7HiY8ALhCufk2X2'
        'g2W5VlwMxAD20o+kGf3rRF+IFAMJQHwD0Wb/PNzszMlAMkgnJhp+HSHLbDBwfE/A8BuTfHnxGcCq5pPXvmDnpfsvPv76/enp6TV1'
        'ntzI8jpvcZv9cXmSLG6TGZVCWrfc+o3Fv5uDmRDKLL7iNP8hCy6zRfy6DnzA4+ubIQi1wbiVRWAzWy5hNhEZdJMgXMNsnIpOo5st'
        'EDj1JpEp7p4yTBP3Y5yK7JHD27ju5B8SsstjRZjOJNy+hLvBf/YzUrP6RVg+Z76BS8k/dXBsJm39QU5ZMgXmugKcSmYwSGbt/UNm'
        'OfjXCFbY/cSl5NuRfxQUtOthHthBm8rnnxzU/FIa1W4lUPNNaGT+Tqj5XP9oY/4nRqgFj2jkAUmo+QdpZL4V1PxNNDLfHWr+MhqZ'
        'HwA1fwGNzHeBmr+WRuabQM3fSyPz5aHmn6GN8e9hBdAL2pi/B9YgpVHwVEPN96KR+UpQ82tpY/wRWPCfpm3uVaKN8Qdgzq+gifHf'
        'tGFt1Ac0MT8N3kqnifGzYMazPaSF8Qvhbeh6Whg/jRlmvMlv6pv+IxnebOW8QX3jzxjAjWdeR/1GQwEzotU9i+qmtwohNeo7qGz6'
        'gxpk09kWUjfDrvVgQu7tCB2gouFvFwWhjRvY3qWW2S/X5ukzoXXVOLr/UiOhX19b6yODpZfpco0SY38+PbNldlOakxwT9v6x7nay'
        'jd5mriFBYDTCaCkFQfNVDL/hTD77KAvyLnyma7ZR3NLH7QHdCqo007B6QDRgOrX6KOgeYNNLmkvVQhjuAQHDoMplV6hef3z1CMlu'
        'nLH+zLv/o2AUjIJRMApGAfEAAAlgAP4='
    ),
}


# ── Palette ───────────────────────────────────────────────────────────────


def _rgb(hex_code: str) -> tuple:
    hex_code = hex_code.lstrip("#")
    return tuple(int(hex_code[i:i + 2], 16) / 255 for i in (0, 2, 4))


GREEN = _rgb("1F4D3A")
LIGHT_GREEN = _rgb("E8F0EA")
GOLD = _rgb("C9A227")
LIGHT_GOLD = _rgb("F6EFD6")
RED = _rgb("C8322B")
RED_DARK = _rgb("8B1E19")
GREY_TEXT = _rgb("6B7280")
BODY_TEXT = _rgb("1F2937")
UNSELECTED_SEAT = _rgb("D5DBD7")
DIVIDER_GREY = _rgb("D1D5DB")
PILL_PAID_BG = _rgb("DCFCE7")
PILL_PAID_TEXT = _rgb("166534")
PILL_PENDING_BG = _rgb("FEF3C7")
PILL_PENDING_TEXT = _rgb("92400E")
PLACEHOLDER_GREY = _rgb("BFBFBF")
WHITE = (1.0, 1.0, 1.0)

# ── Decorative frame ─────────────────────────────────────────────────────

FRAME_OUTER_INSET = 16
FRAME_THIN_INSET = 26  # no longer drawn as its own box — kept as a footer position reference
KASAVU_INSET = 23
CONTENT_MARGIN = 40

# ── Venue seat grid — mirrors the real hall layout in
# plugins/seat-selection/backend/seat_selection_logic.py's own _ROW_SPECS
# (duplicated here per this module's copied-module convention — see the
# module docstring; keep in sync if the venue shape ever changes there).
#
# Each row is (leftCount, rightCount) — the centre block is always
# CENTER_COUNT seats. Every row's blankEdges*2 + left + center + right
# there sums to a constant ROW_SLOT_WIDTH slots, which is what keeps both
# aisle gaps at the same slot position (LEFT_AISLE_SLOT/RIGHT_AISLE_SLOT)
# on every row regardless of how many real seats it has.
ROW_BLOCK_COUNTS = (
    (5, 5),
    (6, 6),
    (7, 7),
) + tuple((8, 8) for _ in range(16))
GRID_ROWS = len(ROW_BLOCK_COUNTS)  # 19
CENTER_COUNT = 11
ROW_SLOT_WIDTH = 27  # blankEdges*2 + left + CENTER_COUNT + right, constant across every row
LEFT_AISLE_SLOT = 8   # abs slot (0-based) where the left/centre aisle gap starts
RIGHT_AISLE_SLOT = 19  # abs slot where the centre/right aisle gap starts
GRID_PITCH = 7.5
GRID_SEAT_SIZE = 5.6
GRID_AISLE_GAP = 7

# A horizontal walkway crosses the hall after this many rows — mirrors
# seat_selection_logic.py's own WALKWAY_AFTER_ROW; purely a cosmetic gap,
# not a seat-numbering one.
WALKWAY_AFTER_ROW = 10
WALKWAY_GAP = 7

_SEAT_ID_RE = re.compile(r"^[Rr](\d{1,2})[Cc](\d{1,2})$")

# ── Sponsor logos — optional. Left empty by default: the footer then shows
# numbered placeholder slots instead. Each entry is a
# (name, base64_zlib_compressed_raw_rgb_blob, width, height) tuple, baked
# in the same way the WMF Japan logo above is (see _LOGO_RGB_B64's own
# regeneration snippet in the module docstring) — e.g. for a 120x60 PNG:
#
#     from PIL import Image
#     import zlib, base64
#     im = Image.open("sponsor.png").convert("RGBA")
#     bg = Image.new("RGB", im.size, (255, 255, 255))
#     bg.paste(im, mask=im.split()[3])
#     print(bg.size)
#     print(base64.b64encode(zlib.compress(bg.tobytes(), 9)).decode())
#
# then add ("Sponsor Name", "<printed base64>", *bg.size) to this list.
SPONSORS: list = []


# ── Text measurement (no real font metrics available without a library —
# this is a deliberately simple per-character heuristic, generous enough
# that wrapping/centering stays inside its column without needing exact
# glyph widths). ──────────────────────────────────────────────────────────

_NARROW_CHARS = set("iIl.,:;'`!|jJ")
_WIDE_CHARS = set("mMWw@%")


def _char_width_em(ch: str, bold: bool) -> float:
    if ch == " ":
        return 0.28
    if ch in _NARROW_CHARS:
        return 0.32 if bold else 0.28
    if ch in _WIDE_CHARS:
        return 0.98 if bold else 0.92
    base = 0.62 if bold else 0.56
    return base + 0.06 if ch.isupper() else base


def _text_width(s: str, size: float, bold: bool = False) -> float:
    return sum(_char_width_em(c, bold) for c in str(s or "")) * size


# Real Helvetica-Bold glyph widths (1/1000 em, keyed by cp1252/WinAnsi code
# point) — unlike _text_width's own per-character heuristic above (good
# enough when a single Tj lets the renderer's own font metrics lay out a
# whole line), this is used where several separate Tj calls have to be
# manually positioned end-to-end within one line (the iroha glyph sits
# between two text runs in a seat annotation) — there, a "generous"
# estimate compounds into a visible gap by the second seat on the same
# line. Regenerate with:
#     from reportlab.pdfbase.pdfmetrics import stringWidth
#     {code: round(stringWidth(bytes([code]).decode("cp1252"), "Helvetica-Bold", 1000))
#      for code in range(32, 256)}  # skip codes cp1252 leaves undefined
_HELV_BOLD_WIDTHS_1000 = {
    32: 278, 33: 333, 34: 474, 35: 556, 36: 556, 37: 889, 38: 722, 39: 238, 40: 333, 41: 333,
    42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278, 48: 556, 49: 556, 50: 556, 51: 556,
    52: 556, 53: 556, 54: 556, 55: 556, 56: 556, 57: 556, 58: 333, 59: 333, 60: 584, 61: 584,
    62: 584, 63: 611, 64: 975, 65: 722, 66: 722, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778,
    72: 722, 73: 278, 74: 556, 75: 722, 76: 611, 77: 833, 78: 722, 79: 778, 80: 667, 81: 778,
    82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944, 88: 667, 89: 667, 90: 611, 91: 333,
    92: 278, 93: 333, 94: 584, 95: 556, 96: 333, 97: 556, 98: 611, 99: 556, 100: 611, 101: 556,
    102: 333, 103: 611, 104: 611, 105: 278, 106: 278, 107: 556, 108: 278, 109: 889, 110: 611, 111: 611,
    112: 611, 113: 611, 114: 389, 115: 556, 116: 333, 117: 611, 118: 556, 119: 778, 120: 556, 121: 556,
    122: 500, 123: 389, 124: 280, 125: 389, 126: 584, 128: 556, 130: 278, 131: 556, 132: 500, 133: 1000,
    134: 556, 135: 556, 136: 333, 137: 1000, 138: 667, 139: 333, 140: 1000, 142: 611, 145: 278, 146: 278,
    147: 500, 148: 500, 149: 350, 150: 556, 151: 1000, 152: 333, 153: 1000, 154: 667, 155: 333, 156: 944,
    158: 611, 159: 722, 160: 278, 161: 333, 162: 556, 163: 556, 164: 556, 165: 556, 166: 280, 167: 556,
    168: 333, 169: 737, 170: 370, 171: 556, 172: 584, 173: 333, 174: 737, 175: 333, 176: 400, 177: 584,
    178: 333, 179: 333, 180: 333, 181: 611, 182: 556, 183: 278, 184: 333, 185: 333, 186: 365, 187: 556,
    188: 834, 189: 834, 190: 834, 191: 611, 192: 722, 193: 722, 194: 722, 195: 722, 196: 722, 197: 722,
    198: 1000, 199: 722, 200: 667, 201: 667, 202: 667, 203: 667, 204: 278, 205: 278, 206: 278, 207: 278,
    208: 722, 209: 722, 210: 778, 211: 778, 212: 778, 213: 778, 214: 778, 215: 584, 216: 778, 217: 722,
    218: 722, 219: 722, 220: 722, 221: 667, 222: 667, 223: 611, 224: 556, 225: 556, 226: 556, 227: 556,
    228: 556, 229: 556, 230: 889, 231: 556, 232: 556, 233: 556, 234: 556, 235: 556, 236: 278, 237: 278,
    238: 278, 239: 278, 240: 611, 241: 611, 242: 611, 243: 611, 244: 611, 245: 611, 246: 611, 247: 584,
    248: 611, 249: 611, 250: 611, 251: 611, 252: 611, 253: 556, 254: 611, 255: 556,
}


def _exact_bold_text_width(s: str, size: float) -> float:
    """Real Helvetica-Bold width for `s` — see _HELV_BOLD_WIDTHS_1000."""
    return size * sum(_HELV_BOLD_WIDTHS_1000.get(ord(c), 600) for c in str(s or "")) / 1000.0


def _wrap_to_width(text: str, max_width: float, size: float, bold: bool = False) -> list:
    words = str(text or "").split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        candidate = f"{cur} {w}"
        if _text_width(candidate, size, bold) > max_width:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    lines.append(cur)
    return lines


def _pdf_str(s) -> bytes:
    """Encode text for a PDF Tj string. Helvetica's base font only covers
    WinAnsiEncoding (~cp1252); anything outside that (e.g. CJK names) comes
    out as "?" rather than crashing — the tradeoff of a font-embedding-free
    PDF (see module docstring)."""
    encoded = str(s or "").encode("cp1252", errors="replace")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


# ── PDF document/object plumbing ─────────────────────────────────────────


class _PDF:
    """Accumulates PDF objects and renders the final file (header, objects,
    xref table, trailer) — the whole document structure, hand-written."""

    def __init__(self):
        self._objects: list = []

    def add(self, body: bytes) -> int:
        self._objects.append(body)
        return len(self._objects)

    def add_stream(self, header: bytes, data: bytes) -> int:
        return self.add(header + b"\nstream\n" + data + b"\nendstream")

    def reserve(self) -> int:
        """Allocate an object number without content yet — for a forward
        reference (e.g. a Page's /Parent needs the Pages object's number
        before Pages can be built, since Pages' own /Kids needs the
        Page's number first). Fill it in with set()."""
        return self.add(b"")

    def set(self, num: int, body: bytes) -> None:
        self._objects[num - 1] = body

    def render(self, root: int) -> bytes:
        out = bytearray(b"%PDF-1.4\n")
        offsets = [0] * (len(self._objects) + 1)
        for i, body in enumerate(self._objects, start=1):
            offsets[i] = len(out)
            out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
        xref_pos = len(out)
        out += f"xref\n0 {len(self._objects) + 1}\n".encode("ascii")
        out += b"0000000000 65535 f \n"
        for i in range(1, len(self._objects) + 1):
            out += f"{offsets[i]:010d} 00000 n \n".encode("ascii")
        out += (
            b"trailer\n"
            + f"<< /Size {len(self._objects) + 1} /Root {root} 0 R >>\n".encode("ascii")
            + b"startxref\n"
            + f"{xref_pos}\n".encode("ascii")
            + b"%%EOF"
        )
        return bytes(out)


def _logo_image_object(pdf: "_PDF") -> int:
    compressed = base64.b64decode(_LOGO_RGB_B64)
    header = (
        f"<< /Type /XObject /Subtype /Image /Width {_LOGO_W} /Height {_LOGO_H} "
        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
        f"/Length {len(compressed)} >>"
    ).encode("ascii")
    return pdf.add_stream(header, compressed)


def _qr_gray_bitmap(matrix: list, module_pixels: int = 6) -> tuple:
    """Raw 8-bit DeviceGray pixel data for a QR matrix — no PNG involved,
    just a flat byte-per-pixel grid the PDF embeds directly."""
    modcount = len(matrix)
    size = modcount * module_pixels
    raw = bytearray(size * size)
    for row_idx, row in enumerate(matrix):
        py0 = row_idx * module_pixels
        for col_idx, dark in enumerate(row):
            value = 0 if dark else 255
            px0 = col_idx * module_pixels
            fill = bytes([value]) * module_pixels
            for dy in range(module_pixels):
                offset = (py0 + dy) * size + px0
                raw[offset:offset + module_pixels] = fill
    return bytes(raw), size, size


def _image_object(pdf: "_PDF", width: int, height: int, colorspace: str, raw: bytes) -> int:
    compressed = zlib.compress(raw, 9)
    header = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"/ColorSpace /{colorspace} /BitsPerComponent 8 /Filter /FlateDecode "
        f"/Length {len(compressed)} >>"
    ).encode("ascii")
    return pdf.add_stream(header, compressed)


def _iroha_glyph_object(pdf: "_PDF", char: str) -> int:
    """One baked iroha-character bitmap (see _IROHA_GLYPHS_B64 above) as a
    grayscale Image XObject."""
    compressed = base64.b64decode(_IROHA_GLYPHS_B64[char])
    header = (
        f"<< /Type /XObject /Subtype /Image /Width {_IROHA_GLYPH_W} /Height {_IROHA_GLYPH_H} "
        f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
        f"/Length {len(compressed)} >>"
    ).encode("ascii")
    return pdf.add_stream(header, compressed)


# ── Vector content-stream primitives ─────────────────────────────────────


class _Content:
    """Accumulates content-stream drawing operators for the one page. Every
    shape helper below builds a raw PDF path (moveto/lineto/curveto) and
    paints it with fill/stroke/dash state set immediately before — PDF has
    no persistent "current style" worth relying on across calls, so each
    call is self-contained."""

    def __init__(self):
        self.ops: list = []

    def _raw(self, s: str) -> None:
        self.ops.append(s.encode("ascii"))

    def _paint_prefix(self, fill, stroke, line_width, dash) -> str:
        parts = ["[] 0 d" if dash is None else f"[{' '.join(str(p) for p in dash)}] 0 d"]
        if line_width is not None:
            parts.append(f"{line_width:.2f} w")
        if fill is not None:
            parts.append(f"{fill[0]:.3f} {fill[1]:.3f} {fill[2]:.3f} rg")
        if stroke is not None:
            parts.append(f"{stroke[0]:.3f} {stroke[1]:.3f} {stroke[2]:.3f} RG")
        return " ".join(parts)

    @staticmethod
    def _paint_op(fill, stroke) -> str:
        if fill is not None and stroke is not None:
            return "B"
        if fill is not None:
            return "f"
        if stroke is not None:
            return "S"
        return "n"

    def _paint_path(self, path: str, fill, stroke, line_width, dash) -> None:
        prefix = self._paint_prefix(fill, stroke, line_width, dash)
        op = self._paint_op(fill, stroke)
        self._raw(f"{prefix} {path} {op}")
        if dash is not None:
            self._raw("[] 0 d")

    # ── Text ──────────────────────────────────────────────────────────

    def text(self, x: float, y: float, s: str, *, font: str = "F1", size: float = 10,
              color: tuple = BODY_TEXT) -> None:
        r, g, b = color
        self.ops.append(
            f"{r:.3f} {g:.3f} {b:.3f} rg BT /{font} {size:.2f} Tf {x:.2f} {y:.2f} Td ".encode("ascii")
            + b"(" + _pdf_str(s) + b") Tj ET"
        )

    def text_centered(self, cx: float, y: float, s: str, *, font: str = "F1", size: float = 10,
                       color: tuple = BODY_TEXT, bold: bool = False) -> None:
        width = _text_width(s, size, bold=(font == "F2"))
        self.text(cx - width / 2, y, s, font=font, size=size, color=color)

    def text_rotated90(self, x: float, y: float, s: str, *, font: str = "F1", size: float = 10,
                        color: tuple = BODY_TEXT) -> None:
        """Text rotated 90 degrees counter-clockwise, baseline running
        bottom-to-top through (x, y) — used for the "AISLE" label. Uses the
        full text matrix (Tm) rather than Td, since Td can only translate."""
        r, g, b = color
        self.ops.append(
            f"{r:.3f} {g:.3f} {b:.3f} rg BT /{font} {size:.2f} Tf 0 1 -1 0 {x:.2f} {y:.2f} Tm ".encode("ascii")
            + b"(" + _pdf_str(s) + b") Tj ET"
        )

    # ── Shapes ────────────────────────────────────────────────────────

    def rect(self, x: float, y: float, w: float, h: float, *, fill=None, stroke=None,
              line_width: float = 1.0) -> None:
        self._paint_path(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re", fill, stroke, line_width, None)

    def line(self, x0: float, y0: float, x1: float, y1: float, *, color=(0, 0, 0),
              width: float = 1.0, dash=None) -> None:
        self._paint_path(f"{x0:.2f} {y0:.2f} m {x1:.2f} {y1:.2f} l", None, color, width, dash)

    @staticmethod
    def _rounded_rect_path(x: float, y: float, w: float, h: float, r: float) -> str:
        k = r * 0.5523
        return (
            f"{x + r:.2f} {y:.2f} m "
            f"{x + w - r:.2f} {y:.2f} l "
            f"{x + w - r + k:.2f} {y:.2f} {x + w:.2f} {y + r - k:.2f} {x + w:.2f} {y + r:.2f} c "
            f"{x + w:.2f} {y + h - r:.2f} l "
            f"{x + w:.2f} {y + h - r + k:.2f} {x + w - r + k:.2f} {y + h:.2f} {x + w - r:.2f} {y + h:.2f} c "
            f"{x + r:.2f} {y + h:.2f} l "
            f"{x + r - k:.2f} {y + h:.2f} {x:.2f} {y + h - r + k:.2f} {x:.2f} {y + h - r:.2f} c "
            f"{x:.2f} {y + r:.2f} l "
            f"{x:.2f} {y + r - k:.2f} {x + r - k:.2f} {y:.2f} {x + r:.2f} {y:.2f} c h"
        )

    def rounded_rect(self, x: float, y: float, w: float, h: float, r: float, *, fill=None,
                       stroke=None, line_width: float = 1.0, dash=None) -> None:
        self._paint_path(self._rounded_rect_path(x, y, w, h, r), fill, stroke, line_width, dash)

    def top_rounded_rect(self, x: float, y: float, w: float, h: float, r: float, *, fill=None,
                           stroke=None, line_width: float = 1.0) -> None:
        """Rounded top corners, square bottom corners — for a header bar
        that seams flush into the card body below it."""
        k = r * 0.5523
        path = (
            f"{x:.2f} {y:.2f} m "
            f"{x + w:.2f} {y:.2f} l "
            f"{x + w:.2f} {y + h - r:.2f} l "
            f"{x + w:.2f} {y + h - r + k:.2f} {x + w - r + k:.2f} {y + h:.2f} {x + w - r:.2f} {y + h:.2f} c "
            f"{x + r:.2f} {y + h:.2f} l "
            f"{x + r - k:.2f} {y + h:.2f} {x:.2f} {y + h - r + k:.2f} {x:.2f} {y + h - r:.2f} c h"
        )
        self._paint_path(path, fill, stroke, line_width, None)

    def circle(self, cx: float, cy: float, r: float, *, fill=None, stroke=None,
                line_width: float = 1.0) -> None:
        k = r * 0.5523
        path = (
            f"{cx - r:.2f} {cy:.2f} m "
            f"{cx - r:.2f} {cy + k:.2f} {cx - k:.2f} {cy + r:.2f} {cx:.2f} {cy + r:.2f} c "
            f"{cx + k:.2f} {cy + r:.2f} {cx + r:.2f} {cy + k:.2f} {cx + r:.2f} {cy:.2f} c "
            f"{cx + r:.2f} {cy - k:.2f} {cx + k:.2f} {cy - r:.2f} {cx:.2f} {cy - r:.2f} c "
            f"{cx - k:.2f} {cy - r:.2f} {cx - r:.2f} {cy - k:.2f} {cx - r:.2f} {cy:.2f} c h"
        )
        self._paint_path(path, fill, stroke, line_width, None)

    def triangle_up(self, cx: float, cy: float, half: float, *, fill=None, stroke=None,
                      line_width: float = 0.5) -> None:
        """A small upward-pointing triangle — used instead of the Unicode
        "▲" glyph, which Helvetica's WinAnsiEncoding doesn't cover (see
        _pdf_str's module note) and would render as "?"."""
        path = (
            f"{cx:.2f} {cy + half:.2f} m {cx + half:.2f} {cy - half:.2f} l "
            f"{cx - half:.2f} {cy - half:.2f} l h"
        )
        self._paint_path(path, fill, stroke, line_width, None)

    def diamond(self, cx: float, cy: float, half: float, *, fill=None, stroke=None,
                 line_width: float = 0.5) -> None:
        path = (
            f"{cx:.2f} {cy + half:.2f} m {cx + half:.2f} {cy:.2f} l "
            f"{cx:.2f} {cy - half:.2f} l {cx - half:.2f} {cy:.2f} l h"
        )
        self._paint_path(path, fill, stroke, line_width, None)

    def stage_shape(self, x: float, y: float, w: float, h: float, bulge: float, *, fill=None,
                     stroke=None, line_width: float = 1.0) -> None:
        """A rectangle with a single cubic-bezier dome on top — PDF has no
        native arc/ellipse operator, only bezier curves, so this is the
        simplest shape that still reads as a stage riser."""
        c1x, c2x = x + w * 0.22, x + w * 0.78
        top = y + h
        path = (
            f"{x:.2f} {y:.2f} m {x:.2f} {top:.2f} l "
            f"{c1x:.2f} {top + bulge:.2f} {c2x:.2f} {top + bulge:.2f} {x + w:.2f} {top:.2f} c "
            f"{x + w:.2f} {y:.2f} l h"
        )
        self._paint_path(path, fill, stroke, line_width, None)

    def image(self, name: str, x: float, y: float, w: float, h: float) -> None:
        self._raw(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /{name} Do Q")

    def build(self) -> bytes:
        return b"\n".join(self.ops)


# ── Decorative kasavu-style border ────────────────────────────────────────


def _draw_kasavu_band(content: "_Content") -> None:
    inset = KASAVU_INSET
    half = 1.5
    step = 12
    start = FRAME_OUTER_INSET + 24
    end_x = PAGE_W - FRAME_OUTER_INSET - 24
    end_y = PAGE_H - FRAME_OUTER_INSET - 24
    x = start
    while x < end_x:
        content.diamond(x, PAGE_H - inset, half, fill=GOLD)
        content.diamond(x, inset, half, fill=GOLD)
        x += step
    y = start
    while y < end_y:
        content.diamond(inset, y, half, fill=GOLD)
        content.diamond(PAGE_W - inset, y, half, fill=GOLD)
        y += step


def _draw_corner_ornament(content: "_Content", cx: float, cy: float) -> None:
    content.circle(cx, cy, 9, fill=WHITE, stroke=GREEN, line_width=1.2)
    content.diamond(cx, cy, 5, fill=GOLD)
    content.circle(cx, cy, 1.6, fill=RED)


def _draw_border(content: "_Content") -> None:
    outer = FRAME_OUTER_INSET
    content.rect(outer, outer, PAGE_W - 2 * outer, PAGE_H - 2 * outer, stroke=GREEN, line_width=2.2)
    _draw_kasavu_band(content)
    for cx, cy in (
        (outer, outer), (PAGE_W - outer, outer),
        (outer, PAGE_H - outer), (PAGE_W - outer, PAGE_H - outer),
    ):
        _draw_corner_ornament(content, cx, cy)


# ── Header ────────────────────────────────────────────────────────────────


def _draw_header(content: "_Content") -> float:
    """Logo + title on the left, QR box on the right, a full-width divider
    under both. Returns the y to start the body at."""
    top = PAGE_H - CONTENT_MARGIN
    left = CONTENT_MARGIN
    right = PAGE_W - CONTENT_MARGIN

    logo_w, logo_h = _LOGO_W * 0.6333, _LOGO_H * 0.6333  # ~114x62, same aspect as the source bitmap
    content.image("Im1", left, top - logo_h, logo_w, logo_h)

    title_x = left + logo_w + 16
    content.text(title_x, top - 20, "Guest Check-In Ticket", font="F2", size=22, color=GREEN)

    qr_disp = 96
    qr_pad = 9
    qr_box = qr_disp + 2 * qr_pad
    qr_x = right - qr_box
    qr_y = top - qr_box
    content.rounded_rect(qr_x, qr_y, qr_box, qr_box, 8, fill=WHITE, stroke=GOLD, line_width=1.2)
    content.image("Im2", qr_x + qr_pad, qr_y + qr_pad, qr_disp, qr_disp)
    content.text_centered(qr_x + qr_box / 2, qr_y - 11, "Scan at entry", font="F1", size=7.5, color=GREY_TEXT)

    header_bottom = min(top - logo_h, qr_y - 18) - 10
    content.line(left, header_bottom, right, header_bottom, color=GREEN, width=1.4)
    return header_bottom - 22


# ── Left column: info sections ────────────────────────────────────────────

LEFT_COL_W = 255
VALUE_OFFSET = 72
ROW_GAP = 14
SECTION_HEADING_GAP = 20
SECTION_TRAILING_GAP = 24

# (font_size, row_gap) tiers for the column's open-ended rows — the Seats
# row (one entry per selected seat) and the Celebrity Dinner category rows
# (up to four). Everything else in this column is 1-3 fixed lines and
# always uses the first tier; these two can grow enough on a big
# group/family booking to run into the page's fixed footer position, so
# build_check_in_ticket_pdf picks the smallest scale (tier index) that
# keeps the whole column within the space actually available above the
# footer, applying it to both row kinds together.
_ROW_STYLE_TIERS = ((9.5, ROW_GAP), (8.5, 12), (7.5, 10), (6.5, 8.5), (6.0, 7.5))


def _row_style(scale: int) -> tuple:
    return _ROW_STYLE_TIERS[min(max(scale, 0), len(_ROW_STYLE_TIERS) - 1)]


def _status_kind(status: str) -> str:
    status = str(status or "").strip().lower()
    if status == "paid":
        return "paid"
    if status in {"pending", "pending_payment", "unpaid", "awaiting_payment", "request", "requested"}:
        return "pending"
    return "plain"


def _draw_status_pill(content: "_Content", x: float, y: float, label: str, kind: str) -> None:
    label = label.upper()
    size = 8
    text_w = _text_width(label, size, bold=True)
    pad_x = 8
    pill_w = text_w + 2 * pad_x
    pill_h = 14
    bg, fg = (PILL_PAID_BG, PILL_PAID_TEXT) if kind == "paid" else (PILL_PENDING_BG, PILL_PENDING_TEXT)
    content.rounded_rect(x, y, pill_w, pill_h, pill_h / 2, fill=bg)
    content.text(x + pad_x, y + 4, label, font="F2", size=size, color=fg)


def _section_rows_height(rows: list, scale: int = 0) -> float:
    """Vertical space `rows` will take, matching _draw_section's own
    arithmetic exactly (so the left/right column height-matching pass can
    measure without a _Content to draw into)."""
    value_w = LEFT_COL_W - VALUE_OFFSET - 10
    h = 0.0
    for label, value, kind in rows:
        if kind in ("paid", "pending"):
            h += ROW_GAP
            continue
        if kind == "seats":
            size, gap = _row_style(scale)
            h += gap * len(_wrap_seat_segments(value, value_w, size))
            continue
        if kind == "dinner_cat":
            _size, gap = _row_style(scale)
            h += gap
            continue
        wrapped = _wrap_to_width(str(value), value_w, 9.5, bold=True)
        h += ROW_GAP * len(wrapped)
    return h


def _draw_section(content, pdf: "_PDF", xobjects: list, x: float, y: float, title: str,
                    rows: list, scale: int = 0) -> float:
    """rows: list of (label, value, kind) — kind is "paid"/"pending" for a
    status pill, "seats" for a seat-id list annotated with iroha row-letter
    glyphs (see _wrap_seat_segments/_draw_seat_segments), "dinner_cat" for a
    single already-formatted Celebrity Dinner category row, or "plain" for
    ordinary wrapped text. `scale` picks the _ROW_STYLE_TIERS entry used by
    "seats"/"dinner_cat" rows (see its own comment)."""
    value_w = LEFT_COL_W - VALUE_OFFSET - 10
    content.text(x, y, title, font="F2", size=11, color=GREEN)
    y -= SECTION_HEADING_GAP
    for label, value, kind in rows:
        if kind in ("paid", "pending"):
            content.text(x, y - 3, label, font="F1", size=9.5, color=GREY_TEXT)
            _draw_status_pill(content, x + VALUE_OFFSET, y - 5, str(value), kind)
            y -= ROW_GAP
            continue
        if kind == "seats":
            size, gap = _row_style(scale)
            wrapped = _wrap_seat_segments(value, value_w, size)
            content.text(x, y, label, font="F1", size=9.5, color=GREY_TEXT)
            for line in wrapped:
                _draw_seat_segments(content, pdf, xobjects, x + VALUE_OFFSET, y, line,
                                      size=size, color=BODY_TEXT)
                y -= gap
            continue
        if kind == "dinner_cat":
            size, gap = _row_style(scale)
            content.text(x, y, label, font="F1", size=size, color=GREY_TEXT)
            content.text(x + VALUE_OFFSET, y, str(value), font="F2", size=size, color=BODY_TEXT)
            y -= gap
            continue
        wrapped = _wrap_to_width(str(value), value_w, 9.5, bold=True)
        content.text(x, y, label, font="F1", size=9.5, color=GREY_TEXT)
        content.text(x + VALUE_OFFSET, y, wrapped[0], font="F2", size=9.5, color=BODY_TEXT)
        y -= ROW_GAP
        for extra in wrapped[1:]:
            content.text(x + VALUE_OFFSET, y, extra, font="F2", size=9.5, color=BODY_TEXT)
            y -= ROW_GAP
    y -= 6
    content.line(x, y, x + LEFT_COL_W, y, color=DIVIDER_GREY, width=0.75)
    return y - (SECTION_TRAILING_GAP - 6)


# (display label, veg field, non-veg field) — mirrors
# lambdas/celebrity_dinner_emailer.py's own DINNER_CATEGORIES/_guest_rows
# (copied rather than imported, same convention as the rest of this file).
# Field names match celebrity_dinner_logic.py's normalize_dinner_
# registration; a zone-upgrade-bundled dinner add-on is normalized into
# this same shape before it ever reaches this module (see qr_email.py's
# and seat_purchase_emailer.py's own _dinner_from_zone_upgrade).
DINNER_CATEGORIES = (
    ("Adults", "numberOfAdultsVeg", "numberOfAdultsNonVeg"),
    ("Kids (9-14)", "numberOfKids914Veg", "numberOfKids914NonVeg"),
    ("Kids (6-8)", "numberOfKids68Veg", "numberOfKids68NonVeg"),
    ("Kids (under 6)", "numberOfKidsUnder6Veg", "numberOfKidsUnder6NonVeg"),
)


def _dinner_guest_rows(celebrity_dinner: dict) -> list:
    """One (label, "N (V veg, NV non-veg)") row per age category with at
    least one guest — categories with nothing booked are omitted. Same
    presentation as the confirmation email's own guest breakdown."""
    rows = []
    for label, veg_field, non_veg_field in DINNER_CATEGORIES:
        veg = int(celebrity_dinner.get(veg_field, 0) or 0)
        non_veg = int(celebrity_dinner.get(non_veg_field, 0) or 0)
        if veg + non_veg <= 0:
            continue
        rows.append((label, f"{veg + non_veg} ({veg} veg, {non_veg} non-veg)"))
    return rows


def _build_left_sections(event_name: str, registrant_name: str, zone_upgrade: dict,
                          celebrity_dinner: dict, seat_selection: dict) -> list:
    """[(title, rows), ...] — the data side of the left column, independent
    of drawing/measuring, so both passes use exactly the same content."""
    sections = [("Event Information", [
        ("Event", event_name or "Event", "plain"),
        ("Registrant", registrant_name or "-", "plain"),
    ])]

    if zone_upgrade:
        rows = [("Tier", zone_upgrade.get("tierName") or "-", "plain")]
        seat_count = zone_upgrade.get("seatCount")
        if seat_count:
            rows.append(("Seats", str(seat_count), "plain"))
        status = str(zone_upgrade.get("status") or "").strip()
        if status:
            kind = _status_kind(status)
            rows.append(("Status", status.title() if kind == "plain" else status, kind))
        sections.append(("Zone Upgrade", rows))

    if celebrity_dinner:
        rows = [(label, value, "dinner_cat") for label, value in _dinner_guest_rows(celebrity_dinner)]
        if not rows:
            rows = [("Guests", "0", "plain")]
        status = str(celebrity_dinner.get("status") or "").strip()
        if status:
            kind = _status_kind(status)
            rows.append(("Status", status.title() if kind == "plain" else status, kind))
        sections.append(("Celebrity Dinner", rows))

    if seat_selection:
        seat_ids = seat_selection.get("seatIds") or []
        rows = [("Seats", seat_ids, "seats")]
        status = str(seat_selection.get("status") or "").strip()
        if status:
            kind = _status_kind(status)
            rows.append(("Status", status.title() if kind == "plain" else status, kind))
        sections.append(("Seat Selection", rows))

    return sections


def _left_column_height(sections: list, scale: int = 0) -> float:
    y = 0.0
    for _, rows in sections:
        y -= SECTION_HEADING_GAP
        y -= _section_rows_height(rows, scale)
        y -= (SECTION_TRAILING_GAP)
    return -y


def _draw_left_column(content: "_Content", pdf: "_PDF", xobjects: list, x: float, top_y: float,
                        sections: list, scale: int = 0) -> float:
    y = top_y
    for title, rows in sections:
        y = _draw_section(content, pdf, xobjects, x, y, title, rows, scale)
    return y


# ── Seat parsing (R08C12 -> row 8, col 12) ────────────────────────────────


def _row_block_counts(row: int) -> tuple:
    return ROW_BLOCK_COUNTS[row - 1]


def _row_max_col(row: int) -> int:
    left, right = _row_block_counts(row)
    return left + CENTER_COUNT + right


def _seat_block(row: int, col: int) -> str:
    """Which of the row's three seating blocks a column falls in — needed
    because block sizes vary per row (see ROW_BLOCK_COUNTS), so a fixed
    column threshold would misplace seats on the narrower rows."""
    left, _right = _row_block_counts(row)
    if col <= left:
        return "left"
    if col <= left + CENTER_COUNT:
        return "center"
    return "right"


def _abs_slot(row: int, col: int) -> int:
    """The column's position on the constant ROW_SLOT_WIDTH-slot ruler
    shared by every row, regardless of that row's own left/right block
    sizes — see the ROW_BLOCK_COUNTS comment for why this works out to a
    fixed LEFT_AISLE_SLOT/RIGHT_AISLE_SLOT on every row."""
    left, _right = _row_block_counts(row)
    if col <= left:
        return (LEFT_AISLE_SLOT - left) + (col - 1)
    if col <= left + CENTER_COUNT:
        return LEFT_AISLE_SLOT + (col - left - 1)
    return RIGHT_AISLE_SLOT + (col - left - CENTER_COUNT - 1)


# ── Seat display annotation — "R08C12" -> "R08C12 (と-12 · Center)" ──────
#
# Mirrors seat_purchase_emailer.py's own _seat_display exactly (same
# copied-module convention as the rest of this file), so the printed row
# letter/seat number/block shown here always matches the confirmation
# email. The iroha row letter is drawn from _IROHA_GLYPHS_B64 above rather
# than as text, since Helvetica's WinAnsiEncoding can't render it.

_IROHA_LABELS = (
    "い", "ろ", "は", "に", "ほ", "へ", "と", "ち", "り",
    "ぬ", "る", "を", "わ", "か", "よ", "た", "れ", "そ", "つ",
)
_BLOCK_DISPLAY_NAMES = {"left": "Left", "center": "Center", "right": "Right"}


def _printed_seat_number(row: int, col: int) -> tuple:
    """(number, block) — the venue's own printed seat number (which skips
    some numbers in the Left block; see seat_selection_logic.py's own
    docstring for why) and which block it's in."""
    left, _right = _row_block_counts(row)
    if col <= left:
        number = 9 if col == left else col + 8 - left
        block = "left"
    elif col <= left + CENTER_COUNT:
        number = col - left + 8
        block = "center"
    else:
        number = col - left - CENTER_COUNT + 19
        block = "right"
    return number, block


def _seat_display_segments(seat_id: str) -> list:
    """[("text"|"glyph", value), ...] segments for one seat id, e.g.
    "R08C12" -> [("text", "R08C12 ("), ("glyph", "と"), ("text", "-12 ·
    Center)")]. Falls back to a single plain-text segment for anything
    that doesn't parse or sits outside the venue's real layout."""
    seat_id = str(seat_id or "").strip()
    match = _SEAT_ID_RE.match(seat_id)
    if not match:
        return [("text", seat_id)]
    row, col = int(match.group(1)), int(match.group(2))
    if row < 1 or row > GRID_ROWS or col < 1 or col > _row_max_col(row):
        return [("text", seat_id)]
    number, block = _printed_seat_number(row, col)
    return [
        ("text", f"{seat_id} ("),
        ("glyph", _IROHA_LABELS[row - 1]),
        ("text", f"-{number} · {_BLOCK_DISPLAY_NAMES[block]})"),
    ]


def _glyph_width(size: float) -> float:
    """A glyph's drawn width for a given "font size" — height is always
    size, width follows the baked bitmap's own (nearly square) aspect."""
    return size * _IROHA_GLYPH_W / _IROHA_GLYPH_H


def _segments_width(segments: list, size: float) -> float:
    """Uses _exact_bold_text_width (not the cheaper _text_width heuristic)
    for the text pieces — several separate Tj calls end up positioned
    end-to-end on one line here (see _draw_seat_segments), so this needs
    to match the real rendered width, not just a wrapping-safe estimate."""
    return sum(_glyph_width(size) if kind == "glyph" else _exact_bold_text_width(val, size)
               for kind, val in segments)


def _wrap_seat_segments(seat_ids: list, max_width: float, size: float) -> list:
    """Wraps comma-separated seat annotations into lines no wider than
    max_width, same idea as _wrap_to_width but operating on
    _seat_display_segments' mixed text/glyph tokens instead of words — each
    seat's whole annotation is atomic and is never split across lines."""
    atoms = [_seat_display_segments(s) for s in seat_ids] or [[("text", "-")]]
    sep = [("text", ", ")]
    sep_w = _exact_bold_text_width(", ", size)
    lines = []
    cur, cur_w = list(atoms[0]), _segments_width(atoms[0], size)
    for atom in atoms[1:]:
        atom_w = _segments_width(atom, size)
        if cur_w + sep_w + atom_w > max_width:
            lines.append(cur)
            cur, cur_w = list(atom), atom_w
        else:
            cur = cur + sep + atom
            cur_w += sep_w + atom_w
    lines.append(cur)
    return lines


def _draw_seat_segments(content: "_Content", pdf: "_PDF", xobjects: list, x: float, y: float,
                          segments: list, *, size: float, color: tuple) -> None:
    cx = x
    for kind, val in segments:
        if kind == "glyph":
            gw = _glyph_width(size)
            img_name = f"Ir{len(xobjects)}"
            xobjects.append((img_name, _iroha_glyph_object(pdf, val)))
            content.image(img_name, cx, y - size * _IROHA_GLYPH_DESCENT_FRAC, gw, size)
            cx += gw
        else:
            content.text(cx, y, val, font="F2", size=size, color=color)
            cx += _exact_bold_text_width(val, size)


def _parse_seat_ids(seat_ids: list) -> tuple:
    """{"row": {col, ...}}, plus a list of ids that didn't parse or fall
    outside the venue's real per-row seat count — those are logged and
    still included in the summary text, just not drawn on the grid."""
    by_row: dict = {}
    unmatched: list = []
    for sid in seat_ids or []:
        match = _SEAT_ID_RE.match(str(sid).strip())
        if not match:
            logger.warning("checkin_pdf: seat id %r doesn't match R##C## — showing in summary only", sid)
            unmatched.append(str(sid))
            continue
        row, col = int(match.group(1)), int(match.group(2))
        if row < 1 or row > GRID_ROWS or col < 1 or col > _row_max_col(row):
            logger.warning(
                "checkin_pdf: seat %s is outside the venue's row %d layout — showing in summary only",
                sid, row,
            )
            unmatched.append(str(sid))
            continue
        by_row.setdefault(row, set()).add(col)
    return by_row, unmatched


def _contiguous_runs(row: int, cols: list) -> list:
    """Groups sorted column numbers into contiguous runs, splitting
    whenever the seating block changes even if the columns are numerically
    adjacent — e.g. the last Left seat and first Centre seat are +1 apart
    but sit on opposite sides of a physical aisle, so they must never be
    drawn/summarised as one run."""
    if not cols:
        return []
    runs = []
    start = prev = cols[0]
    prev_block = _seat_block(row, prev)
    for c in cols[1:]:
        block = _seat_block(row, c)
        if c == prev + 1 and block == prev_block:
            prev = c
        else:
            runs.append((start, prev))
            start = prev = c
        prev_block = block
    runs.append((start, prev))
    return runs


# ── Venue map card (right column) ─────────────────────────────────────────

MAP_PAD = 14
MAP_HEADER_H = 22
MAP_GAP_AFTER_HEADER = 14
MAP_STAGE_H = 20
MAP_GAP_AFTER_STAGE = 12
MAP_COL_HEADER_H = 10
MAP_GAP_AFTER_GRID = 12
MAP_ENTRANCE_H = 15
MAP_GAP_AFTER_ENTRANCE = 10
MAP_LEGEND_H = 10
MAP_GAP_AFTER_LEGEND = 8
MAP_NOTE_H = 9

# Header ruler marks, as abs slot positions — the boundary of each block on
# the venue's widest (16 identical) rows: seat 1, the last Left seat (8),
# the first Centre seat (9), the last Centre seat (19), the first Right
# seat (20), and the last Right seat (27). Narrower rows (1–3) just leave
# some of these positions empty, which reads as the row tapering inward.
_ABS_SLOT_HEADER_MARKS = (0, 7, 8, 18, 19, 26)


def _grid_col_x(grid_x0: float, abs_slot: int) -> float:
    if abs_slot < LEFT_AISLE_SLOT:
        return grid_x0 + abs_slot * GRID_PITCH
    if abs_slot < RIGHT_AISLE_SLOT:
        return grid_x0 + LEFT_AISLE_SLOT * GRID_PITCH + GRID_AISLE_GAP + (abs_slot - LEFT_AISLE_SLOT) * GRID_PITCH
    return (
        grid_x0 + LEFT_AISLE_SLOT * GRID_PITCH + GRID_AISLE_GAP
        + (RIGHT_AISLE_SLOT - LEFT_AISLE_SLOT) * GRID_PITCH + GRID_AISLE_GAP
        + (abs_slot - RIGHT_AISLE_SLOT) * GRID_PITCH
    )


def _grid_width() -> float:
    return ROW_SLOT_WIDTH * GRID_PITCH + 2 * GRID_AISLE_GAP


def _row_y(grid_top: float, row: int) -> float:
    """A row's top y-coordinate, inserting WALKWAY_GAP once for every row
    past the walkway so rows 11+ sit that much further down."""
    extra = WALKWAY_GAP if row > WALKWAY_AFTER_ROW else 0
    return grid_top - (row - 1) * GRID_PITCH - extra


def _map_card_height() -> float:
    grid_h = GRID_ROWS * GRID_PITCH + WALKWAY_GAP
    content_h = (
        MAP_HEADER_H + MAP_GAP_AFTER_HEADER + MAP_STAGE_H + MAP_GAP_AFTER_STAGE
        + MAP_COL_HEADER_H + grid_h + MAP_GAP_AFTER_GRID + MAP_ENTRANCE_H + MAP_GAP_AFTER_ENTRANCE
        + MAP_LEGEND_H + MAP_GAP_AFTER_LEGEND + MAP_NOTE_H
    )
    return content_h + 2 * MAP_PAD


def _draw_map_card(content: "_Content", x: float, top_y: float, width: float, height: float,
                    seat_ids: list) -> None:
    card_r = 10
    bottom_y = top_y - height
    content.rounded_rect(x, bottom_y, width, height, card_r, fill=WHITE, stroke=GOLD, line_width=1.2)

    content.top_rounded_rect(x, top_y - MAP_HEADER_H, width, MAP_HEADER_H, card_r, fill=GREEN)
    content.text(x + 12, top_y - MAP_HEADER_H + 7, "Venue Map", font="F2", size=11, color=WHITE)
    right_label = "Your seats highlighted"
    rw = _text_width(right_label, 8)
    content.text(x + width - 12 - rw, top_y - MAP_HEADER_H + 8, right_label, font="F1", size=8, color=WHITE)

    inner_x = x + MAP_PAD
    cursor_y = top_y - MAP_HEADER_H - MAP_GAP_AFTER_HEADER

    stage_w = min(width - 2 * MAP_PAD, _grid_width() + 30)
    stage_x = x + (width - stage_w) / 2
    content.stage_shape(stage_x, cursor_y - MAP_STAGE_H, stage_w, MAP_STAGE_H, bulge=7,
                          fill=LIGHT_GOLD, stroke=GOLD, line_width=1.0)
    content.text_centered(stage_x + stage_w / 2, cursor_y - MAP_STAGE_H / 2 - 3, "STAGE",
                            font="F2", size=8, color=GOLD)
    cursor_y -= MAP_STAGE_H + MAP_GAP_AFTER_STAGE

    row_label_w = 16
    grid_x0 = inner_x + row_label_w
    for mark in _ABS_SLOT_HEADER_MARKS:
        col_x = _grid_col_x(grid_x0, mark)
        content.text_centered(col_x + GRID_SEAT_SIZE / 2, cursor_y, str(mark + 1), font="F1", size=6,
                                color=GREY_TEXT)
    cursor_y -= MAP_COL_HEADER_H

    by_row, _unmatched = _parse_seat_ids(seat_ids)
    grid_top = cursor_y
    for row in range(1, GRID_ROWS + 1):
        row_y = _row_y(grid_top, row)
        selected_cols = by_row.get(row, set())
        has_selection = bool(selected_cols)
        content.text(inner_x, row_y - GRID_SEAT_SIZE * 0.7, f"R{row:02d}",
                      font="F2" if has_selection else "F1", size=6.5,
                      color=RED if has_selection else GREY_TEXT)
        for col in range(1, _row_max_col(row) + 1):
            col_x = _grid_col_x(grid_x0, _abs_slot(row, col))
            is_sel = col in selected_cols
            if is_sel:
                content.rounded_rect(col_x, row_y - GRID_SEAT_SIZE, GRID_SEAT_SIZE, GRID_SEAT_SIZE, 1.5,
                                       fill=RED, stroke=RED_DARK, line_width=0.8)
            else:
                content.rounded_rect(col_x, row_y - GRID_SEAT_SIZE, GRID_SEAT_SIZE, GRID_SEAT_SIZE, 1.5,
                                       fill=UNSELECTED_SEAT)
        for run_start, run_end in _contiguous_runs(row, sorted(selected_cols)):
            rx0 = _grid_col_x(grid_x0, _abs_slot(row, run_start)) - 1.5
            rx1 = _grid_col_x(grid_x0, _abs_slot(row, run_end)) + GRID_SEAT_SIZE + 1.5
            content.rounded_rect(rx0, row_y - GRID_SEAT_SIZE - 1.5, rx1 - rx0, GRID_SEAT_SIZE + 3, 2,
                                   stroke=RED, line_width=1.0, dash=(2, 2))

    grid_bottom = _row_y(grid_top, GRID_ROWS) - GRID_SEAT_SIZE
    for aisle_slot in (LEFT_AISLE_SLOT, RIGHT_AISLE_SLOT):
        aisle_x = _grid_col_x(grid_x0, aisle_slot) - GRID_AISLE_GAP / 2
        content.text_rotated90(aisle_x + 2, (grid_top + grid_bottom) / 2 - 14, "AISLE",
                                 font="F1", size=5.5, color=GREY_TEXT)

    walkway_y = (
        _row_y(grid_top, WALKWAY_AFTER_ROW) - GRID_SEAT_SIZE
        + _row_y(grid_top, WALKWAY_AFTER_ROW + 1)
    ) / 2
    grid_right = _grid_col_x(grid_x0, ROW_SLOT_WIDTH - 1) + GRID_SEAT_SIZE
    walkway_label = "WALKWAY"
    label_w = _text_width(walkway_label, 5.5)
    mid_x = (grid_x0 + grid_right) / 2
    content.line(grid_x0, walkway_y, mid_x - label_w / 2 - 4, walkway_y, color=DIVIDER_GREY,
                  width=0.5, dash=(2, 2))
    content.line(mid_x + label_w / 2 + 4, walkway_y, grid_right, walkway_y, color=DIVIDER_GREY,
                  width=0.5, dash=(2, 2))
    content.text_centered(mid_x, walkway_y - 2, walkway_label, font="F1", size=5.5, color=GREY_TEXT)
    cursor_y = grid_bottom - MAP_GAP_AFTER_GRID

    entrance_label = "ENTRANCE"
    triangle_span = 12  # triangle + gap before the label
    ew = _text_width(entrance_label, 8, bold=True) + triangle_span + 20
    entrance_x = x + (width - ew) / 2
    entrance_mid_y = cursor_y - MAP_ENTRANCE_H / 2
    content.rounded_rect(entrance_x, cursor_y - MAP_ENTRANCE_H, ew, MAP_ENTRANCE_H, MAP_ENTRANCE_H / 2,
                           fill=LIGHT_GREEN)
    content.triangle_up(entrance_x + 12, entrance_mid_y, 3.5, fill=GREEN)
    content.text(entrance_x + 12 + triangle_span, entrance_mid_y - 3, entrance_label,
                  font="F2", size=8, color=GREEN)
    cursor_y -= MAP_ENTRANCE_H + MAP_GAP_AFTER_ENTRANCE

    content.rect(inner_x, cursor_y - 7, 8, 8, fill=RED)
    content.text(inner_x + 12, cursor_y - 6, "Your seats", font="F1", size=7.5, color=BODY_TEXT)
    legend2_x = inner_x + 12 + _text_width("Your seats", 7.5) + 14
    content.rect(legend2_x, cursor_y - 7, 8, 8, fill=UNSELECTED_SEAT)
    content.text(legend2_x + 12, cursor_y - 6, "Other seats", font="F1", size=7.5, color=BODY_TEXT)
    cursor_y -= MAP_LEGEND_H + MAP_GAP_AFTER_LEGEND

    content.text(inner_x, cursor_y, "Illustrative layout – not to scale", font="F3", size=7,
                  color=GREY_TEXT)


# ── Footer: note + sponsor strip ──────────────────────────────────────────

FOOTER_NOTE_GAP = 18
SPONSOR_STRIP_H = 56
SPONSOR_HEADING_H = 16
SPONSOR_SLOT_COUNT = 5
SPONSOR_SLOT_H = 30
SPONSOR_SLOT_PAD = 4


def _sponsor_image_object(pdf: "_PDF", png_b64: str, width: int, height: int) -> int:
    compressed = base64.b64decode(png_b64)
    header = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
        f"/Length {len(compressed)} >>"
    ).encode("ascii")
    return pdf.add_stream(header, compressed)


def _draw_footer(content: "_Content", pdf: "_PDF", xobjects: list, top_y: float) -> None:
    left = CONTENT_MARGIN
    right = PAGE_W - CONTENT_MARGIN
    content.text_centered((left + right) / 2, top_y, "Show this ticket – printed or on your phone – at entry.",
                            font="F1", size=9, color=GREY_TEXT)

    strip_top = top_y - FOOTER_NOTE_GAP
    strip_w = right - left
    content.rounded_rect(left, strip_top - SPONSOR_STRIP_H, strip_w, SPONSOR_STRIP_H, 8, fill=LIGHT_GREEN)
    content.text_centered((left + right) / 2, strip_top - 13, "OUR SPONSORS & PARTNERS",
                            font="F2", size=9, color=GREEN)

    slot_gap = 8
    slots_area_w = strip_w - 2 * 12
    slot_w = (slots_area_w - (SPONSOR_SLOT_COUNT - 1) * slot_gap) / SPONSOR_SLOT_COUNT
    slot_y = strip_top - SPONSOR_STRIP_H + 10
    used = SPONSORS[:SPONSOR_SLOT_COUNT]
    n_used = len(used)
    # If there are fewer logos than slots, centre the ones we have instead
    # of leaving them pinned to the left.
    offset_slots = (SPONSOR_SLOT_COUNT - n_used) / 2 if 0 < n_used < SPONSOR_SLOT_COUNT else 0
    for i in range(SPONSOR_SLOT_COUNT):
        slot_x = left + 12 + i * (slot_w + slot_gap)
        content.rounded_rect(slot_x, slot_y, slot_w, SPONSOR_SLOT_H, 4, fill=WHITE)
        sponsor_index = i - int(offset_slots)
        if n_used and 0 <= sponsor_index < n_used:
            name, png_b64, img_w, img_h = used[sponsor_index]
            avail_w = slot_w - 2 * SPONSOR_SLOT_PAD
            avail_h = SPONSOR_SLOT_H - 2 * SPONSOR_SLOT_PAD
            scale = min(avail_w / img_w, avail_h / img_h)
            disp_w, disp_h = img_w * scale, img_h * scale
            img_name = f"Sp{i}"
            obj_num = _sponsor_image_object(pdf, png_b64, img_w, img_h)
            xobjects.append((img_name, obj_num))
            content.image(img_name, slot_x + (slot_w - disp_w) / 2, slot_y + (SPONSOR_SLOT_H - disp_h) / 2,
                            disp_w, disp_h)
        else:
            content.text_centered(slot_x + slot_w / 2, slot_y + SPONSOR_SLOT_H / 2 - 3,
                                    f"Sponsor Logo {i + 1}", font="F1", size=6.5, color=PLACEHOLDER_GREY)


# ── Main entry point ──────────────────────────────────────────────────────


def build_check_in_ticket_pdf(
    *,
    event_name: str,
    registrant_name: str,
    check_in_url: str,
    allocations: list = None,
    zone_upgrade: dict = None,
    celebrity_dinner: dict = None,
    seat_selection: dict = None,
) -> bytes:
    """Build the one-page consolidated ticket PDF and return its raw bytes.

    Same data sources/signature as before this redesign — `allocations` is
    accepted but not shown, matching the explicit "Event, Registrant only"
    Event Information content in this layout. Zone upgrade / celebrity
    dinner / seat selection sections are each skipped when that add-on
    wasn't purchased/booked. No prices are shown anywhere. Returns b"" if
    the qrcode package isn't available (same optional-layer pattern as
    seat_purchase_qr.build_check_in_qr).
    """
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError:
        return b""

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=2)
    qr.add_data(check_in_url)
    qr.make(fit=True)
    qr_raw, qr_w, qr_h = _qr_gray_bitmap(qr.get_matrix())

    pdf = _PDF()
    font_helvetica = pdf.add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    font_bold = pdf.add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    font_italic = pdf.add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>"
    )
    logo_obj = _logo_image_object(pdf)
    qr_obj = _image_object(pdf, qr_w, qr_h, "DeviceGray", qr_raw)
    xobjects = [("Im1", logo_obj), ("Im2", qr_obj)]

    content = _Content()
    _draw_border(content)

    y = _draw_header(content)

    sections = _build_left_sections(
        event_name, registrant_name, zone_upgrade, celebrity_dinner, seat_selection
    )
    left_x = CONTENT_MARGIN
    footer_top = FRAME_THIN_INSET + 10 + SPONSOR_STRIP_H + SPONSOR_HEADING_H + FOOTER_NOTE_GAP + 8
    available_h = y - footer_top

    # A big group/family booking can have a dozen-plus annotated seats
    # and/or all four Celebrity Dinner age categories — pick the smallest
    # _ROW_STYLE_TIERS scale (falling back to the smallest tier if even
    # that doesn't fit) that keeps the whole column clear of the footer.
    scale = 0
    for candidate in range(len(_ROW_STYLE_TIERS)):
        left_h = _left_column_height(sections, candidate)
        scale = candidate
        if left_h <= available_h:
            break

    map_gap = 16
    map_x = left_x + LEFT_COL_W + map_gap
    map_w = (PAGE_W - CONTENT_MARGIN) - map_x
    seat_ids = (seat_selection or {}).get("seatIds") or []
    map_h = _map_card_height()

    body_height = max(left_h, map_h)

    _draw_left_column(content, pdf, xobjects, left_x, y, sections, scale)
    _draw_map_card(content, map_x, y, map_w, body_height, seat_ids)

    _draw_footer(content, pdf, xobjects, footer_top)

    content_obj = pdf.add_stream(
        f"<< /Length {len(content.build())} >>".encode("ascii"), content.build()
    )
    fonts_dict = f"/F1 {font_helvetica} 0 R /F2 {font_bold} 0 R /F3 {font_italic} 0 R"
    xobj_dict = " ".join(f"/{name} {num} 0 R" for name, num in xobjects)

    # Page.Parent must point at the Pages object, but Pages.Kids needs the
    # Page's own number first — reserve the Pages slot so the Page can
    # reference it, then fill Pages in once the Page's number is known.
    pages_obj = pdf.reserve()
    page_obj = pdf.add(
        (
            f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << {fonts_dict} >> /XObject << {xobj_dict} >> >> "
            f"/Contents {content_obj} 0 R >>"
        ).encode("ascii")
    )
    pdf.set(pages_obj, f"<< /Type /Pages /Kids [{page_obj} 0 R] /Count 1 >>".encode("ascii"))
    catalog_obj = pdf.add(f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode("ascii"))
    return pdf.render(catalog_obj)


def check_in_filename(registration_id: str) -> str:
    """guest_check_in_attachment_<registrationId>.pdf, shortened if needed."""
    reg_id = str(registration_id or "ticket").strip() or "ticket"
    name = f"guest_check_in_attachment_{reg_id}.pdf"
    if len(name) <= 100:
        return name
    # Keep the id (what makes the filename unique); shorten the prefix instead.
    return f"guest_pass_{reg_id}.pdf"[:100]
