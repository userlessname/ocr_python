#NoEnv
#SingleInstance, Force
SetWorkingDir %A_ScriptDir%
CoordMode, Mouse, Screen
CoordMode, Pixel, Screen
CoordMode, ToolTip, Screen
SendMode Input
; SetBatchLines, -1  ; 1. Maximize script speed (Essential for loops)

; --------------------Find start point-------------------
ImageHandle := LoadPicture("start_point.png")

; -------------------Auto Accept-------------------
; Initialize Toggle State
Toggle := 0

~ScrollLock::
    Toggle := !Toggle
    if (Toggle) {
        ; Show "on" tooltip in center of screen 1
        ToolTip, on, A_ScreenWidth/2, A_ScreenHeight/2
        SetTimer, FindStartPoint, 1000
        SetTimer, CheckImages, 1000
        SetTimer, AutoScroll, 1000
    } else {
        ToolTip
        SetTimer, FindStartPoint, Off
        SetTimer, CheckImages, Off
        SetTimer, AutoScroll, Off
    }
return

FindStartPoint:
    Loop {
        ImageSearch, StartX, StartY, 0, 0, A_ScreenWidth, A_ScreenHeight, *50 HBITMAP:*%ImageHandle%

        if (ErrorLevel = 0) {
            MouseMove, % StartX + 85, % StartY - 115
            break
        }
        Sleep, 32
    }
return

CheckImages:
    ; Search for 1.png
    ImageSearch, FoundX, FoundY, 0, 0, A_ScreenWidth, A_ScreenHeight, *175 1.png
    if (ErrorLevel = 0) {
        MouseGetPos, PrevX, PrevY ; Save current mouse position
        Click, %FoundX%, %FoundY%
        MouseMove, %PrevX%, %PrevY% ; Restore mouse position
        return
    }

    ; Search for 2.png
    ImageSearch, FoundX, FoundY, 0, 0, A_ScreenWidth, A_ScreenHeight, *175 2.png
    if (ErrorLevel = 0) {
        MouseGetPos, PrevX, PrevY ; Save current mouse position
        Click, %FoundX%, %FoundY%
        MouseMove, %PrevX%, %PrevY% ; Restore mouse position
        return
    }
return

AutoScroll:
    Send, {WheelDown}
return

