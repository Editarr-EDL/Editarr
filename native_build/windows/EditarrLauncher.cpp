#define UNICODE
#define _UNICODE

#include <windows.h>
#include <shellapi.h>

#include <filesystem>
#include <string>
#include <vector>

namespace {

constexpr wchar_t kWindowClass[] = L"EditarrTrayWindowV1";
constexpr wchar_t kMutexName[] = L"Local\\EditarrTrayV1";
constexpr wchar_t kRunKey[] = L"Software\\Microsoft\\Windows\\CurrentVersion\\Run";
constexpr wchar_t kRunValue[] = L"Editarr";
constexpr UINT kTrayMessage = WM_APP + 1;
constexpr UINT_PTR kStatusTimer = 1;
constexpr UINT kOpen = 1001;
constexpr UINT kOpenLogs = 1002;
constexpr UINT kStart = 1003;
constexpr UINT kStop = 1004;
constexpr UINT kAutostart = 1005;
constexpr UINT kQuit = 1006;

HINSTANCE g_instance = nullptr;
HWND g_window = nullptr;
NOTIFYICONDATAW g_tray{};

std::filesystem::path ExecutablePath() {
    std::vector<wchar_t> buffer(32768);
    DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) {
        return {};
    }
    return std::filesystem::path(std::wstring(buffer.data(), length));
}

std::filesystem::path RuntimePath() {
    return ExecutablePath().parent_path() / L"Runtime" / L"Editarr.exe";
}

std::wstring Quote(const std::wstring& value) {
    std::wstring quoted = L"\"";
    size_t slashes = 0;
    for (wchar_t character : value) {
        if (character == L'\\') {
            ++slashes;
            continue;
        }
        if (character == L'\"') {
            quoted.append(slashes * 2 + 1, L'\\');
            quoted.push_back(character);
            slashes = 0;
            continue;
        }
        quoted.append(slashes, L'\\');
        slashes = 0;
        quoted.push_back(character);
    }
    quoted.append(slashes * 2, L'\\');
    quoted.push_back(L'\"');
    return quoted;
}

bool RunRuntime(const std::wstring& arguments, bool wait, DWORD* exit_code = nullptr) {
    const auto runtime = RuntimePath();
    if (!std::filesystem::is_regular_file(runtime)) {
        return false;
    }
    std::wstring command = Quote(runtime.wstring());
    if (!arguments.empty()) {
        command += L" " + arguments;
    }
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(L'\0');
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    std::wstring working_directory = runtime.parent_path().wstring();
    if (!CreateProcessW(
            runtime.c_str(), mutable_command.data(), nullptr, nullptr, FALSE,
            CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, nullptr,
            working_directory.c_str(), &startup, &process)) {
        return false;
    }
    CloseHandle(process.hThread);
    if (!wait) {
        CloseHandle(process.hProcess);
        return true;
    }
    const DWORD result = WaitForSingleObject(process.hProcess, 15000);
    DWORD code = 1;
    if (result == WAIT_OBJECT_0) {
        GetExitCodeProcess(process.hProcess, &code);
    }
    CloseHandle(process.hProcess);
    if (exit_code != nullptr) {
        *exit_code = code;
    }
    return result == WAIT_OBJECT_0 && code == 0;
}

bool IsRunning() {
    return RunRuntime(L"lifecycle status", true);
}

bool StartRuntime(bool background) {
    if (IsRunning()) {
        return true;
    }
    return RunRuntime(background ? L"--no-browser" : L"", false);
}

bool StopRuntime() {
    if (!IsRunning()) {
        return true;
    }
    return RunRuntime(L"lifecycle quit", true);
}

bool WaitForStop() {
    for (int attempt = 0; attempt < 60; ++attempt) {
        if (!IsRunning()) {
            return true;
        }
        Sleep(500);
    }
    return false;
}

std::wstring AutostartCommand() {
    return Quote(ExecutablePath().wstring()) + L" --background";
}

bool IsAutostartEnabled() {
    HKEY key = nullptr;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, kRunKey, 0, KEY_QUERY_VALUE, &key) != ERROR_SUCCESS) {
        return false;
    }
    wchar_t value[32768]{};
    DWORD type = 0;
    DWORD size = sizeof(value);
    const LONG result = RegQueryValueExW(
        key, kRunValue, nullptr, &type, reinterpret_cast<BYTE*>(value), &size);
    RegCloseKey(key);
    return result == ERROR_SUCCESS && type == REG_SZ && value == AutostartCommand();
}

bool SetAutostart(bool enabled) {
    HKEY key = nullptr;
    if (RegCreateKeyExW(
            HKEY_CURRENT_USER, kRunKey, 0, nullptr, 0, KEY_SET_VALUE,
            nullptr, &key, nullptr) != ERROR_SUCCESS) {
        return false;
    }
    LONG result = ERROR_SUCCESS;
    if (enabled) {
        const std::wstring value = AutostartCommand();
        result = RegSetValueExW(
            key, kRunValue, 0, REG_SZ,
            reinterpret_cast<const BYTE*>(value.c_str()),
            static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t)));
    } else {
        result = RegDeleteValueW(key, kRunValue);
        if (result == ERROR_FILE_NOT_FOUND) {
            result = ERROR_SUCCESS;
        }
    }
    RegCloseKey(key);
    return result == ERROR_SUCCESS;
}

void NotifyFailure(const wchar_t* action) {
    std::wstring message = L"Unable to ";
    message += action;
    message += L". Open Editarr Diagnostics from the Start menu and inspect the logs.";
    MessageBoxW(g_window, message.c_str(), L"Editarr", MB_OK | MB_ICONERROR);
}

void UpdateTooltip() {
    const wchar_t* text = IsRunning() ? L"Editarr - Running" : L"Editarr - Stopped";
    lstrcpynW(g_tray.szTip, text, ARRAYSIZE(g_tray.szTip));
    Shell_NotifyIconW(NIM_MODIFY, &g_tray);
}

void OpenEditarr() {
    if (IsRunning()) {
        if (!RunRuntime(L"lifecycle open-ui", true)) {
            NotifyFailure(L"open Editarr");
        }
    } else if (!StartRuntime(false)) {
        NotifyFailure(L"start Editarr");
    }
}

void ShowMenu() {
    const bool running = IsRunning();
    HMENU menu = CreatePopupMenu();
    AppendMenuW(menu, MF_STRING | MF_DISABLED, 0, running ? L"Status: Running" : L"Status: Stopped");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, kOpen, L"Open Editarr");
    AppendMenuW(menu, MF_STRING, kOpenLogs, L"Open Logs");
    AppendMenuW(menu, MF_STRING | (running ? MF_GRAYED : 0), kStart, L"Start");
    AppendMenuW(menu, MF_STRING | (running ? 0 : MF_GRAYED), kStop, L"Stop");
    AppendMenuW(menu, MF_STRING | (IsAutostartEnabled() ? MF_CHECKED : 0), kAutostart, L"Launch at Login");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, kQuit, L"Quit");
    POINT point{};
    GetCursorPos(&point);
    SetForegroundWindow(g_window);
    TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN, point.x, point.y, 0, g_window, nullptr);
    DestroyMenu(menu);
}

void RemoveTray() {
    if (g_tray.cbSize != 0) {
        Shell_NotifyIconW(NIM_DELETE, &g_tray);
        g_tray.cbSize = 0;
    }
}

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == kTrayMessage) {
        if (LOWORD(lparam) == WM_LBUTTONUP) {
            OpenEditarr();
        } else if (LOWORD(lparam) == WM_RBUTTONUP || LOWORD(lparam) == WM_CONTEXTMENU) {
            ShowMenu();
        }
        return 0;
    }
    if (message == WM_TIMER && wparam == kStatusTimer) {
        UpdateTooltip();
        return 0;
    }
    if (message == WM_COMMAND) {
        switch (LOWORD(wparam)) {
        case kOpen:
            OpenEditarr();
            break;
        case kOpenLogs:
            if (!RunRuntime(L"lifecycle open-logs", true)) {
                NotifyFailure(L"open the log folder");
            }
            break;
        case kStart:
            if (!StartRuntime(false)) {
                NotifyFailure(L"start Editarr");
            }
            break;
        case kStop:
            if (!StopRuntime()) {
                NotifyFailure(L"stop Editarr");
            }
            break;
        case kAutostart:
            if (!SetAutostart(!IsAutostartEnabled())) {
                NotifyFailure(L"change Launch at Login");
            }
            break;
        case kQuit:
            StopRuntime();
            DestroyWindow(window);
            break;
        }
        UpdateTooltip();
        return 0;
    }
    if (message == WM_DESTROY) {
        RemoveTray();
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

bool CreateTrayWindow() {
    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = WindowProcedure;
    window_class.hInstance = g_instance;
    window_class.lpszClassName = kWindowClass;
    if (!RegisterClassExW(&window_class) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        return false;
    }
    g_window = CreateWindowExW(
        0, kWindowClass, L"Editarr", 0, 0, 0, 0, 0,
        HWND_MESSAGE, nullptr, g_instance, nullptr);
    if (!g_window) {
        return false;
    }
    g_tray.cbSize = sizeof(g_tray);
    g_tray.hWnd = g_window;
    g_tray.uID = 1;
    g_tray.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP;
    g_tray.uCallbackMessage = kTrayMessage;
    g_tray.hIcon = static_cast<HICON>(LoadImageW(
        g_instance, MAKEINTRESOURCEW(1), IMAGE_ICON, 0, 0, LR_DEFAULTSIZE));
    lstrcpynW(g_tray.szTip, L"Editarr - Starting", ARRAYSIZE(g_tray.szTip));
    if (!g_tray.hIcon || !Shell_NotifyIconW(NIM_ADD, &g_tray)) {
        return false;
    }
    g_tray.uVersion = NOTIFYICON_VERSION_4;
    Shell_NotifyIconW(NIM_SETVERSION, &g_tray);
    SetTimer(g_window, kStatusTimer, 3000, nullptr);
    return true;
}

int CommandMode(const std::wstring& command) {
    if (command == L"--status") {
        return IsRunning() ? 0 : 1;
    }
    if (command == L"--start") {
        return StartRuntime(true) ? 0 : 1;
    }
    if (command == L"--stop") {
        return StopRuntime() && WaitForStop() ? 0 : 1;
    }
    if (command == L"--open") {
        OpenEditarr();
        return 0;
    }
    if (command == L"--enable-autostart") {
        return SetAutostart(true) ? 0 : 1;
    }
    if (command == L"--disable-autostart") {
        return SetAutostart(false) ? 0 : 1;
    }
    if (command == L"--quit") {
        HWND tray = FindWindowExW(HWND_MESSAGE, nullptr, kWindowClass, L"Editarr");
        if (tray) {
            PostMessageW(tray, WM_COMMAND, kQuit, 0);
        } else if (!StopRuntime()) {
            return 1;
        }
        return WaitForStop() ? 0 : 1;
    }
    return -1;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    g_instance = instance;
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    const std::wstring command = argc == 2 ? argv[1] : L"";
    if (argv) {
        LocalFree(argv);
    }
    if (!command.empty() && command != L"--background") {
        const int result = CommandMode(command);
        return result >= 0 ? result : 2;
    }

    HANDLE mutex = CreateMutexW(nullptr, TRUE, kMutexName);
    if (!mutex) {
        return 1;
    }
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(mutex);
        if (command != L"--background") {
            RunRuntime(L"", false);
        }
        return 0;
    }
    if (!StartRuntime(command == L"--background") || !CreateTrayWindow()) {
        StopRuntime();
        CloseHandle(mutex);
        return 1;
    }
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    StopRuntime();
    ReleaseMutex(mutex);
    CloseHandle(mutex);
    return 0;
}
