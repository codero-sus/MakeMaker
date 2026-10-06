/*
 * mknative -- the C companion to MakeMaker.
 *
 * MakeMaker's Python half renders templates and scaffolds projects; this
 * program does the parts that are naturally native:
 *
 *   mknative doctor [--json]     probe the host and its toolchain
 *   mknative build  <project>    run the project's build recipe
 *   mknative run    <project>    run the project's run recipe
 *   mknative test   <project>    run the project's test recipe
 *   mknative clean  <project>    run the project's clean recipe
 *   mknative info   <project>    show the recorded project metadata
 *   mknative version
 *
 * Recipes are read from .makemaker/project.json, which the generator writes.
 * C11, POSIX, no external dependencies.
 */

#define _POSIX_C_SOURCE 200809L

#include <ctype.h>
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

#include "mknative.h"

#define PROJECT_META_DIR ".makemaker"
#define PROJECT_META_FILE "project.json"

/* ------------------------------------------------------------------ */
/* small utilities                                                     */
/* ------------------------------------------------------------------ */

static void die(const char *format, ...) {
    va_list args;
    fputs("mknative: error: ", stderr);
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
    exit(1);
}

static void *xmalloc(size_t size) {
    void *pointer = malloc(size ? size : 1);
    if (!pointer) {
        die("out of memory");
    }
    return pointer;
}

static char *xstrdup(const char *text) {
    size_t length = strlen(text) + 1;
    char *copy = xmalloc(length);
    memcpy(copy, text, length);
    return copy;
}

/* ------------------------------------------------------------------ */
/* minimal JSON reader                                                 */
/* ------------------------------------------------------------------ */

typedef enum {
    JSON_NULL,
    JSON_BOOL,
    JSON_NUMBER,
    JSON_STRING,
    JSON_ARRAY,
    JSON_OBJECT
} json_type;

typedef struct json_value json_value;

struct json_value {
    json_type type;
    char *string;          /* JSON_STRING payload, or member key */
    double number;
    int boolean;
    json_value **items;    /* array elements / object values */
    char **keys;           /* object keys (NULL for arrays) */
    size_t count;
    size_t capacity;
};

typedef struct {
    const char *text;
    size_t position;
} json_parser;

static void json_free(json_value *value) {
    if (!value) {
        return;
    }
    for (size_t index = 0; index < value->count; index++) {
        json_free(value->items[index]);
        if (value->keys) {
            free(value->keys[index]);
        }
    }
    free(value->items);
    free(value->keys);
    free(value->string);
    free(value);
}

static json_value *json_new(json_type type) {
    json_value *value = xmalloc(sizeof(*value));
    memset(value, 0, sizeof(*value));
    value->type = type;
    return value;
}

static void json_append(json_value *container, char *key, json_value *child) {
    if (container->count == container->capacity) {
        container->capacity = container->capacity ? container->capacity * 2 : 8;
        container->items = realloc(container->items, container->capacity * sizeof(*container->items));
        if (!container->items) {
            die("out of memory");
        }
        if (container->type == JSON_OBJECT) {
            container->keys = realloc(container->keys, container->capacity * sizeof(*container->keys));
            if (!container->keys) {
                die("out of memory");
            }
        }
    }
    container->items[container->count] = child;
    if (container->type == JSON_OBJECT) {
        container->keys[container->count] = key;
    }
    container->count++;
}

static void json_skip_space(json_parser *parser) {
    while (parser->text[parser->position] && isspace((unsigned char)parser->text[parser->position])) {
        parser->position++;
    }
}

static json_value *json_parse_value(json_parser *parser);

static char *json_parse_string(json_parser *parser) {
    if (parser->text[parser->position] != '"') {
        die("expected a JSON string at offset %zu", parser->position);
    }
    parser->position++;

    size_t capacity = 32;
    size_t length = 0;
    char *buffer = xmalloc(capacity);

    for (;;) {
        char character = parser->text[parser->position];
        if (character == '\0') {
            die("unterminated JSON string");
        }
        if (character == '"') {
            parser->position++;
            break;
        }
        if (character == '\\') {
            parser->position++;
            char escaped = parser->text[parser->position];
            switch (escaped) {
            case 'n': character = '\n'; break;
            case 't': character = '\t'; break;
            case 'r': character = '\r'; break;
            case 'b': character = '\b'; break;
            case 'f': character = '\f'; break;
            case '"': character = '"'; break;
            case '\\': character = '\\'; break;
            case '/': character = '/'; break;
            case 'u': {
                /* Decode \uXXXX into UTF-8; enough for the metadata we write. */
                unsigned code = 0;
                for (int digit = 0; digit < 4; digit++) {
                    parser->position++;
                    char hex = parser->text[parser->position];
                    code <<= 4;
                    if (hex >= '0' && hex <= '9') code |= (unsigned)(hex - '0');
                    else if (hex >= 'a' && hex <= 'f') code |= (unsigned)(hex - 'a' + 10);
                    else if (hex >= 'A' && hex <= 'F') code |= (unsigned)(hex - 'A' + 10);
                    else die("bad \\u escape in JSON string");
                }
                char utf8[4];
                size_t written = 0;
                if (code < 0x80) {
                    utf8[written++] = (char)code;
                } else if (code < 0x800) {
                    utf8[written++] = (char)(0xC0 | (code >> 6));
                    utf8[written++] = (char)(0x80 | (code & 0x3F));
                } else {
                    utf8[written++] = (char)(0xE0 | (code >> 12));
                    utf8[written++] = (char)(0x80 | ((code >> 6) & 0x3F));
                    utf8[written++] = (char)(0x80 | (code & 0x3F));
                }
                for (size_t index = 0; index < written; index++) {
                    if (length + 1 >= capacity) {
                        capacity *= 2;
                        buffer = realloc(buffer, capacity);
                        if (!buffer) die("out of memory");
                    }
                    buffer[length++] = utf8[index];
                }
                parser->position++;
                continue;
            }
            default:
                die("unknown escape '\\%c' in JSON string", escaped);
            }
            parser->position++;
        } else {
            parser->position++;
        }
        if (length + 1 >= capacity) {
            capacity *= 2;
            buffer = realloc(buffer, capacity);
            if (!buffer) {
                die("out of memory");
            }
        }
        buffer[length++] = character;
    }
    buffer[length] = '\0';
    return buffer;
}

static json_value *json_parse_value(json_parser *parser) {
    json_skip_space(parser);
    char character = parser->text[parser->position];

    if (character == '"') {
        json_value *value = json_new(JSON_STRING);
        value->string = json_parse_string(parser);
        return value;
    }
    if (character == '{') {
        parser->position++;
        json_value *object = json_new(JSON_OBJECT);
        json_skip_space(parser);
        if (parser->text[parser->position] == '}') {
            parser->position++;
            return object;
        }
        for (;;) {
            json_skip_space(parser);
            char *key = json_parse_string(parser);
            json_skip_space(parser);
            if (parser->text[parser->position] != ':') {
                die("expected ':' after JSON object key");
            }
            parser->position++;
            json_value *child = json_parse_value(parser);
            json_append(object, key, child);
            json_skip_space(parser);
            char separator = parser->text[parser->position];
            if (separator == ',') {
                parser->position++;
                continue;
            }
            if (separator == '}') {
                parser->position++;
                return object;
            }
            die("expected ',' or '}' in JSON object");
        }
    }
    if (character == '[') {
        parser->position++;
        json_value *array = json_new(JSON_ARRAY);
        json_skip_space(parser);
        if (parser->text[parser->position] == ']') {
            parser->position++;
            return array;
        }
        for (;;) {
            json_value *child = json_parse_value(parser);
            json_append(array, NULL, child);
            json_skip_space(parser);
            char separator = parser->text[parser->position];
            if (separator == ',') {
                parser->position++;
                continue;
            }
            if (separator == ']') {
                parser->position++;
                return array;
            }
            die("expected ',' or ']' in JSON array");
        }
    }
    if (strncmp(parser->text + parser->position, "true", 4) == 0) {
        parser->position += 4;
        json_value *value = json_new(JSON_BOOL);
        value->boolean = 1;
        return value;
    }
    if (strncmp(parser->text + parser->position, "false", 5) == 0) {
        parser->position += 5;
        json_value *value = json_new(JSON_BOOL);
        value->boolean = 0;
        return value;
    }
    if (strncmp(parser->text + parser->position, "null", 4) == 0) {
        parser->position += 4;
        return json_new(JSON_NULL);
    }
    if (character == '-' || isdigit((unsigned char)character)) {
        char *end = NULL;
        double number = strtod(parser->text + parser->position, &end);
        if (end == parser->text + parser->position) {
            die("malformed JSON number at offset %zu", parser->position);
        }
        parser->position = (size_t)(end - parser->text);
        json_value *value = json_new(JSON_NUMBER);
        value->number = number;
        return value;
    }
    die("unexpected character '%c' in JSON at offset %zu", character ? character : '?', parser->position);
    return NULL;
}

static json_value *json_parse(const char *text) {
    json_parser parser = {text, 0};
    json_value *value = json_parse_value(&parser);
    json_skip_space(&parser);
    return value;
}

static const json_value *json_get(const json_value *object, const char *key) {
    if (!object || object->type != JSON_OBJECT) {
        return NULL;
    }
    for (size_t index = 0; index < object->count; index++) {
        if (object->keys[index] && strcmp(object->keys[index], key) == 0) {
            return object->items[index];
        }
    }
    return NULL;
}

static const char *json_string(const json_value *value, const char *fallback) {
    if (value && value->type == JSON_STRING && value->string) {
        return value->string;
    }
    return fallback;
}

/* ------------------------------------------------------------------ */
/* project metadata                                                    */
/* ------------------------------------------------------------------ */

static char *project_meta_path(const char *directory) {
    size_t needed = strlen(directory) + sizeof("/" PROJECT_META_DIR "/" PROJECT_META_FILE) + 1;
    char *path = xmalloc(needed);
    snprintf(path, needed, "%s/%s/%s", directory, PROJECT_META_DIR, PROJECT_META_FILE);
    return path;
}

static json_value *load_project(const char *directory) {
    char *path = project_meta_path(directory);
    FILE *handle = fopen(path, "rb");
    if (!handle) {
        die("%s is not a MakeMaker project (missing %s)", directory, path);
    }
    fseek(handle, 0, SEEK_END);
    long size = ftell(handle);
    fseek(handle, 0, SEEK_SET);
    if (size < 0) {
        die("cannot determine the size of %s", path);
    }
    char *buffer = xmalloc((size_t)size + 1);
    size_t read = fread(buffer, 1, (size_t)size, handle);
    buffer[read] = '\0';
    fclose(handle);
    free(path);

    json_value *project = json_parse(buffer);
    free(buffer);
    return project;
}

/* ------------------------------------------------------------------ */
/* command execution                                                   */
/* ------------------------------------------------------------------ */

static int run_command(char *const argv[], const char *directory, int echo) {
    if (echo) {
        fputc('+', stdout);
        for (size_t index = 0; argv[index]; index++) {
            printf(" %s", argv[index]);
        }
        fputc('\n', stdout);
        fflush(stdout);
    }

    pid_t child = fork();
    if (child < 0) {
        die("fork failed: %s", strerror(errno));
    }
    if (child == 0) {
        if (directory && chdir(directory) != 0) {
            fprintf(stderr, "mknative: cannot enter %s: %s\n", directory, strerror(errno));
            _exit(127);
        }
        execvp(argv[0], argv);
        fprintf(stderr, "mknative: cannot run %s: %s\n", argv[0], strerror(errno));
        _exit(127);
    }

    int status = 0;
    if (waitpid(child, &status, 0) < 0) {
        die("waitpid failed: %s", strerror(errno));
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        fprintf(stderr, "mknative: %s killed by signal %d\n", argv[0], WTERMSIG(status));
        return 128 + WTERMSIG(status);
    }
    return 1;
}

/* Run one recipe: an array of arrays of strings. */
static int run_recipe(const json_value *recipe, const char *directory, int echo) {
    if (!recipe || recipe->type != JSON_ARRAY) {
        die("recipe is not a list of commands");
    }
    for (size_t index = 0; index < recipe->count; index++) {
        const json_value *command = recipe->items[index];
        if (command->type != JSON_ARRAY) {
            /* A recipe is a list of commands; refuse to guess what a bare
             * string means instead of silently doing nothing. */
            die("malformed recipe: command %zu is not a list of arguments", index + 1);
        }
        if (command->count == 0) {
            continue;
        }
        char **argv = xmalloc((command->count + 1) * sizeof(*argv));
        for (size_t part = 0; part < command->count; part++) {
            /* execvp wants char*; the strings are never modified. */
            argv[part] = (char *)json_string(command->items[part], "");
        }
        argv[command->count] = NULL;

        int status = run_command(argv, directory, echo);
        free(argv);
        if (status != 0) {
            return status;
        }
    }
    return 0;
}

static int command_recipe(const char *directory, const char *action, int echo) {
    json_value *project = load_project(directory);
    const json_value *commands = json_get(project, "commands");
    const json_value *recipe = json_get(commands, action);
    if (!recipe) {
        fprintf(stderr, "mknative: this project has no '%s' recipe (known:", action);
        if (commands && commands->type == JSON_OBJECT) {
            for (size_t index = 0; index < commands->count; index++) {
                fprintf(stderr, " %s", commands->keys[index]);
            }
        }
        fputs(")\n", stderr);
        json_free(project);
        return 1;
    }
    int status = run_recipe(recipe, directory, echo);
    json_free(project);
    return status;
}

/* ------------------------------------------------------------------ */
/* toolchain probing                                                   */
/* ------------------------------------------------------------------ */

static const char *const TOOL_NAMES[] = {
    "cc", "gcc", "g++", "clang", "clang++", "make", "cmake", "ninja",
    "pkg-config", "python3", "java", "gradle", "dotnet", "xcodebuild",
    "xcrun", "adb", "git", NULL
};

/* Tools whose version flag is not --version. */
static const char *version_args_for(const char *name) {
    if (strcmp(name, "java") == 0) {
        return "-version";
    }
    if (strcmp(name, "xcodebuild") == 0) {
        return "-version";
    }
    return "--version";
}

static char *find_in_path(const char *name) {
    if (strchr(name, '/')) {
        return access(name, X_OK) == 0 ? xstrdup(name) : NULL;
    }
    const char *path = getenv("PATH");
    if (!path || !*path) {
        path = "/usr/local/bin:/usr/bin:/bin";
    }
    size_t name_length = strlen(name);
    const char *cursor = path;
    while (*cursor) {
        const char *separator = strchr(cursor, ':');
        size_t segment = separator ? (size_t)(separator - cursor) : strlen(cursor);
        if (segment == 0) {
            cursor = ".";
            segment = 1;
        }
        size_t needed = segment + name_length + 2;
        char *candidate = xmalloc(needed);
        snprintf(candidate, needed, "%.*s/%s", (int)segment, cursor, name);
        if (access(candidate, X_OK) == 0) {
            return candidate;
        }
        free(candidate);
        if (!separator) {
            break;
        }
        cursor = separator + 1;
    }
    return NULL;
}

/* Run "path --version 2>&1" and return the first line, or an empty string. */
static char *capture_version(const char *path, const char *args) {
    size_t needed = strlen(path) + strlen(args) + 16;
    char *command = xmalloc(needed);
    snprintf(command, needed, "'%s' %s 2>&1", path, args);

    char *result = xstrdup("");
    FILE *handle = popen(command, "r");
    free(command);
    if (!handle) {
        return result;
    }
    char line[1024];
    if (fgets(line, sizeof(line), handle)) {
        size_t length = strlen(line);
        while (length > 0 && (line[length - 1] == '\n' || line[length - 1] == '\r')) {
            line[--length] = '\0';
        }
        free(result);
        result = xstrdup(line);
    }
    pclose(handle);
    return result;
}

static void emit_json_string(FILE *out, const char *text) {
    fputc('"', out);
    for (const char *cursor = text; *cursor; cursor++) {
        unsigned char character = (unsigned char)*cursor;
        switch (character) {
        case '"': fputs("\\\"", out); break;
        case '\\': fputs("\\\\", out); break;
        case '\n': fputs("\\n", out); break;
        case '\r': fputs("\\r", out); break;
        case '\t': fputs("\\t", out); break;
        default:
            if (character < 0x20) {
                fprintf(out, "\\u%04x", character);
            } else {
                fputc(character, out);
            }
        }
    }
    fputc('"', out);
}

typedef struct {
    const char *name;
    char *path;
    char *version;
} tool_info;

static size_t probe_tools(tool_info *tools, size_t limit) {
    size_t count = 0;
    for (size_t index = 0; TOOL_NAMES[index] && count < limit; index++) {
        char *path = find_in_path(TOOL_NAMES[index]);
        if (!path) {
            continue;
        }
        tools[count].name = TOOL_NAMES[index];
        tools[count].path = path;
        tools[count].version = capture_version(path, version_args_for(TOOL_NAMES[index]));
        count++;
    }
    return count;
}

static void free_tools(tool_info *tools, size_t count) {
    for (size_t index = 0; index < count; index++) {
        free(tools[index].path);
        free(tools[index].version);
    }
}

static const char *tool_version(const tool_info *tools, size_t count, const char *name) {
    for (size_t index = 0; index < count; index++) {
        if (strcmp(tools[index].name, name) == 0) {
            return tools[index].version;
        }
    }
    return NULL;
}

static int command_doctor(int as_json) {
    struct utsname system_info;
    memset(&system_info, 0, sizeof(system_info));
    if (uname(&system_info) != 0) {
        snprintf(system_info.sysname, sizeof(system_info.sysname), "unknown");
        snprintf(system_info.release, sizeof(system_info.release), "unknown");
        snprintf(system_info.machine, sizeof(system_info.machine), "unknown");
    }

    tool_info tools[32];
    size_t count = probe_tools(tools, sizeof(tools) / sizeof(tools[0]));
    const char *python = tool_version(tools, count, "python3");

    if (as_json) {
        fputs("{\n", stdout);
        fputs("  \"host\": {\n", stdout);
        fputs("    \"system\": ", stdout); emit_json_string(stdout, system_info.sysname); fputs(",\n", stdout);
        fputs("    \"release\": ", stdout); emit_json_string(stdout, system_info.release); fputs(",\n", stdout);
        fputs("    \"machine\": ", stdout); emit_json_string(stdout, system_info.machine); fputs(",\n", stdout);
        fputs("    \"python\": ", stdout); emit_json_string(stdout, python ? python : ""); fputs("\n", stdout);
        fputs("  },\n", stdout);
        fputs("  \"tools\": {\n", stdout);
        for (size_t index = 0; index < count; index++) {
            fputs("    ", stdout);
            emit_json_string(stdout, tools[index].name);
            fputs(": { \"path\": ", stdout);
            emit_json_string(stdout, tools[index].path);
            fputs(", \"version\": ", stdout);
            emit_json_string(stdout, tools[index].version);
            fputs(" }", stdout);
            fputs(index + 1 < count ? ",\n" : "\n", stdout);
        }
        fputs(count ? "  },\n" : "  },\n", stdout);
        fputs("  \"missing\": [", stdout);
        int emitted = 0;
        for (size_t index = 0; TOOL_NAMES[index]; index++) {
            if (find_in_path(TOOL_NAMES[index])) {
                continue;
            }
            fputs(emitted ? ", " : "", stdout);
            emit_json_string(stdout, TOOL_NAMES[index]);
            emitted = 1;
        }
        fputs("]\n", stdout);
        fputs("}\n", stdout);
    } else {
        printf("host: %s %s (%s)\n", system_info.sysname, system_info.release, system_info.machine);
        printf("python: %s\n", python && *python ? python : "(not found)");
        printf("tools:\n");
        for (size_t index = 0; index < count; index++) {
            printf("  %-12s %s\n", tools[index].name,
                   tools[index].version && *tools[index].version ? tools[index].version
                                                                : tools[index].path);
        }
        if (count == 0) {
            printf("  (none found)\n");
        }
    }

    free_tools(tools, count);
    return 0;
}

/* ------------------------------------------------------------------ */
/* info                                                                */
/* ------------------------------------------------------------------ */

static int command_info(const char *directory, int as_json) {
    json_value *project = load_project(directory);

    if (as_json) {
        /* The file is already JSON; print it straight through. */
        char *path = project_meta_path(directory);
        FILE *handle = fopen(path, "r");
        free(path);
        if (!handle) {
            die("cannot reopen the project metadata");
        }
        char buffer[4096];
        size_t read;
        while ((read = fread(buffer, 1, sizeof(buffer), handle)) > 0) {
            fwrite(buffer, 1, read, stdout);
        }
        fclose(handle);
        json_free(project);
        return 0;
    }

    const json_value *generator = json_get(project, "generator");
    const json_value *details = json_get(project, "project");
    const json_value *commands = json_get(project, "commands");

    printf("project  : %s\n", json_string(json_get(details, "name"), "(unnamed)"));
    printf("template : %s\n", json_string(json_get(project, "template"), "?"));
    printf("target   : %s\n", json_string(json_get(project, "target"), "?"));
    printf("flavor   : %s\n", json_string(json_get(project, "flavor"), "-"));
    printf("language : %s\n", json_string(json_get(project, "language"), "-"));
    printf("version  : %s\n", json_string(json_get(details, "version"), "?"));
    printf("generated: %s by %s %s\n",
           json_string(json_get(project, "created_utc"), "?"),
           json_string(json_get(generator, "name"), "makemaker"),
           json_string(json_get(generator, "version"), "?"));
    if (commands && commands->type == JSON_OBJECT && commands->count) {
        printf("recipes  :");
        for (size_t index = 0; index < commands->count; index++) {
            printf(" %s", commands->keys[index]);
        }
        fputc('\n', stdout);
    }

    json_free(project);
    return 0;
}

/* ------------------------------------------------------------------ */
/* entry point                                                         */
/* ------------------------------------------------------------------ */

static void usage(FILE *out) {
    fputs(
        "mknative -- the C companion to MakeMaker " MAKERNATIVE_VERSION "\n"
        "\n"
        "Usage:\n"
        "  mknative doctor [--json]      probe the host and its toolchain\n"
        "  mknative build <project>      run the project's build recipe\n"
        "  mknative run <project>        run the project's run recipe\n"
        "  mknative test <project>       run the project's test recipe\n"
        "  mknative clean <project>      run the project's clean recipe\n"
        "  mknative info <project> [--json]\n"
        "  mknative version\n"
        "\n"
        "A <project> is a directory containing " PROJECT_META_DIR "/" PROJECT_META_FILE ".\n",
        out);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        usage(stderr);
        return 2;
    }

    const char *command = argv[1];

    if (strcmp(command, "version") == 0 || strcmp(command, "--version") == 0) {
        printf("mknative %s\n", MAKERNATIVE_VERSION);
        return 0;
    }
    if (strcmp(command, "help") == 0 || strcmp(command, "--help") == 0 ||
        strcmp(command, "-h") == 0) {
        usage(stdout);
        return 0;
    }
    if (strcmp(command, "doctor") == 0) {
        int as_json = argc > 2 && strcmp(argv[2], "--json") == 0;
        return command_doctor(as_json);
    }
    if (strcmp(command, "info") == 0) {
        if (argc < 3) {
            die("info needs a project directory");
        }
        int as_json = argc > 3 && strcmp(argv[3], "--json") == 0;
        return command_info(argv[2], as_json);
    }
    if (strcmp(command, "build") == 0 || strcmp(command, "run") == 0 ||
        strcmp(command, "test") == 0 || strcmp(command, "clean") == 0) {
        if (argc < 3) {
            die("%s needs a project directory", command);
        }
        struct stat info;
        if (stat(argv[2], &info) != 0 || !S_ISDIR(info.st_mode)) {
            die("no such project directory: %s", argv[2]);
        }
        return command_recipe(argv[2], command, 1);
    }

    fprintf(stderr, "mknative: unknown command '%s'\n\n", command);
    usage(stderr);
    return 2;
}
