import os
import json
import re

root = r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\frontend"
frontend_src = os.path.join(root, "src")
de_json_path = os.path.join(root, "src", "locales", "de.json")
en_json_path = os.path.join(root, "src", "locales", "en.json")

with open(de_json_path, 'r', encoding='utf-8') as f:
    de_dict = json.load(f)

# A tokenizer-based scanner for JS/TS function calls: t(...) or i18n.t(...)
def find_t_calls(text):
    # Returns list of (start_index, end_index, full_call, key, second_arg_is_string, second_arg_raw, third_arg_raw)
    results = []
    # Match t( or i18n.t(
    call_pattern = re.compile(r'\b(t|i18n\.t)\s*\(')
    for m in call_pattern.finditer(text):
        start = m.start()
        i = m.end() # right after '('
        # Parse arguments respecting quotes and brackets
        args = []
        cur_arg = []
        depth_paren = 0
        depth_brace = 0
        depth_bracket = 0
        in_quote = None
        escaped = False

        while i < len(text):
            ch = text[i]
            if in_quote:
                cur_arg.append(ch)
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == in_quote:
                    in_quote = None
            else:
                if ch in ("'", '"', '`'):
                    in_quote = ch
                    cur_arg.append(ch)
                elif ch == '(':
                    depth_paren += 1
                    cur_arg.append(ch)
                elif ch == ')':
                    if depth_paren == 0 and depth_brace == 0 and depth_bracket == 0:
                        # End of function call
                        arg_str = "".join(cur_arg).strip()
                        if arg_str:
                            args.append(arg_str)
                        i += 1
                        break
                    depth_paren -= 1
                    cur_arg.append(ch)
                elif ch == '{':
                    depth_brace += 1
                    cur_arg.append(ch)
                elif ch == '}':
                    depth_brace -= 1
                    cur_arg.append(ch)
                elif ch == '[':
                    depth_bracket += 1
                    cur_arg.append(ch)
                elif ch == ']':
                    depth_bracket -= 1
                    cur_arg.append(ch)
                elif ch == ',' and depth_paren == 0 and depth_brace == 0 and depth_bracket == 0:
                    args.append("".join(cur_arg).strip())
                    cur_arg = []
                else:
                    cur_arg.append(ch)
            i += 1

        end = i
        full_call = text[start:end]
        if len(args) >= 2:
            first = args[0]
            second = args[1]
            # Check if first is a string literal
            if (first.startswith("'") and first.endswith("'")) or (first.startswith('"') and first.endswith('"')):
                key = first[1:-1]
                # Check if second is a string literal (single or double quote or backtick)
                is_str = False
                if (second.startswith("'") and second.endswith("'")) or \
                   (second.startswith('"') and second.endswith('"')) or \
                   (second.startswith('`') and second.endswith('`')):
                    is_str = True
                third = args[2] if len(args) >= 3 else None
                results.append((start, end, full_call, key, is_str, second, third, len(args)))
    return results

if __name__ == '__main__':
    total_string_fallbacks = 0
    with_third_arg = 0
    files_with_fallbacks = {}

    for r, d, files in os.walk(frontend_src):
        for f in files:
            if not f.endswith(('.ts', '.tsx')) or f.endswith(('.test.ts', '.test.tsx')):
                continue
            p = os.path.join(r, f)
            rel = os.path.relpath(p, frontend_src).replace('\\', '/')
            with open(p, 'r', encoding='utf-8', errors='ignore') as fl:
                content = fl.read()
            calls = find_t_calls(content)
            fb_calls = [c for c in calls if c[4]] # is_str
            if fb_calls:
                files_with_fallbacks[rel] = fb_calls
                total_string_fallbacks += len(fb_calls)
                with_third_arg += sum(1 for c in fb_calls if c[6] is not None)

    print(f"Total calls with string fallback: {total_string_fallbacks}")
    print(f"Calls with 3rd argument (e.g. options): {with_third_arg}")
    print(f"Files affected: {len(files_with_fallbacks)}")
    for f, calls in sorted(files_with_fallbacks.items(), key=lambda x: -len(x[1]))[:20]:
        print(f"  {f}: {len(calls)}")
