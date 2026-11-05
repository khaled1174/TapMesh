/* TapMesh contributors, 2026. SPDX-License-Identifier: GPL-3.0-or-later */
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX], resolved[PATH_MAX], python[PATH_MAX], bundle[PATH_MAX];
    uint32_t capacity = sizeof(executable);
    if (_NSGetExecutablePath(executable, &capacity) != 0 || realpath(executable, resolved) == NULL) {
        fputs("TapMesh could not locate its installed MCP launcher.\n", stderr);
        return 70;
    }
    char *last = strrchr(resolved, '/');
    if (last == NULL) return 70;
    *last = '\0';
    if (snprintf(python, sizeof(python), "%s/../Resources/runtime/bin/python3", resolved) >= sizeof(python) ||
        snprintf(bundle, sizeof(bundle), "%s/../..", resolved) >= sizeof(bundle)) return 70;
    char **arguments = calloc((size_t)argc + 9, sizeof(char *));
    if (arguments == NULL) return 70;
    char *fixed[] = {python, "-I", "-B", "-m", "tapmesh_runtime", "stdio", "--app-bundle", bundle};
    for (int i = 0; i < 8; i++) arguments[i] = fixed[i];
    for (int i = 1; i < argc; i++) arguments[i + 7] = argv[i];
    execv(python, arguments);
    free(arguments);
    fputs("TapMesh could not start its bundled MCP runtime. Reinstall the complete app.\n", stderr);
    return 70;
}
