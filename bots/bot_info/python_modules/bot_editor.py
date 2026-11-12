import sys
import os
import re
import ast

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

if len(sys.argv) < 2:
    sys.exit(1)

file_path = sys.argv[1]

while True:
    clear_screen()
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()

    print("\033[36m===================================================================\033[0m")
    print("                     \033[1m\033[35mADVANCED BOT CONFIGURATION\033[0m")
    print("\033[36m===================================================================\033[0m")
    print("\033[33m[!] WARNING: Changes require you to RESTART the bot (Toggle Off -> On)\033[0m\n")

    matches = list(re.finditer(r'^(?P<var>[A-Z_0-9]+)\s*=\s*(?P<val>.+)$', text, re.MULTILINE))
    if not matches:
        print("\033[31mNo editable constants found in this script.\033[0m")
        input("Press Enter to return...")
        sys.exit(0)

    for i, m in enumerate(matches):
        print(f"  \033[35m{i+1}.\033[0m {m.group('var')} \033[90m=\033[0m {m.group('val')}")
    print(f"\n  \033[35m0.\033[0m Go Back to Main Menu\n")

    choice = input("\033[37mSelect a variable to edit (number): \033[0m").strip()
    
    if choice == '0':
        break
    
    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(matches):
            continue
            
        m = matches[idx]
        var_name = m.group('var')
        old_val = m.group('val')

        # --- SPECIAL SUB-MENU FOR TRIGGER KEYWORDS ---
        if var_name == "TRIGGER_KEYWORDS":
            while True:
                clear_screen()
                print(f"\033[36m--- MANAGING KEYWORDS: {var_name} ---\033[0m\n")
                try:
                    kw_list = ast.literal_eval(old_val)
                    if not isinstance(kw_list, list): kw_list = []
                except:
                    kw_list = []
                
                print("\033[33mCurrent Keywords:\033[0m")
                if not kw_list:
                    print("  (None)")
                else:
                    for ki, kw in enumerate(kw_list):
                        print(f"  \033[35m{ki+1}.\033[0m {kw}")
                
                print("\nOptions:")
                print("  \033[36m[A]\033[0m Add a Keyword")
                print("  \033[36m[D]\033[0m Delete a Keyword (by number)")
                print("  \033[36m[C]\033[0m Clear All Keywords")
                print("  \033[36m[0]\033[0m Done / Go Back\n")
                
                k_choice = input("Select an option: ").strip().upper()
                
                if k_choice == '0':
                    break
                elif k_choice == 'A':
                    new_kw = input("\nEnter new keyword: ").strip()
                    if new_kw:
                        kw_list.append(new_kw)
                        new_val_str = str(kw_list)
                        text = text[:m.start('val')] + new_val_str + text[m.end('val'):]
                        with open(file_path, 'w', encoding='utf-8') as f: f.write(text)
                        # Re-parse matches to update index
                        matches = list(re.finditer(r'^(?P<var>[A-Z_0-9]+)\s*=\s*(?P<val>.+)$', text, re.MULTILINE))
                        m = [mx for mx in matches if mx.group('var') == var_name][0]
                        old_val = m.group('val')
                elif k_choice == 'D':
                    del_idx = input("\nEnter the number of the keyword to delete: ").strip()
                    if del_idx.isdigit() and 1 <= int(del_idx) <= len(kw_list):
                        kw_list.pop(int(del_idx) - 1)
                        new_val_str = str(kw_list)
                        text = text[:m.start('val')] + new_val_str + text[m.end('val'):]
                        with open(file_path, 'w', encoding='utf-8') as f: f.write(text)
                        matches = list(re.finditer(r'^(?P<var>[A-Z_0-9]+)\s*=\s*(?P<val>.+)$', text, re.MULTILINE))
                        m = [mx for mx in matches if mx.group('var') == var_name][0]
                        old_val = m.group('val')
                elif k_choice == 'C':
                    new_val_str = "[]"
                    text = text[:m.start('val')] + new_val_str + text[m.end('val'):]
                    with open(file_path, 'w', encoding='utf-8') as f: f.write(text)
                    matches = list(re.finditer(r'^(?P<var>[A-Z_0-9]+)\s*=\s*(?P<val>.+)$', text, re.MULTILINE))
                    m = [mx for mx in matches if mx.group('var') == var_name][0]
                    old_val = m.group('val')

        # --- STANDARD VARIABLE EDITOR ---
        else:
            print(f"\n\033[36mEditing:\033[0m {var_name}")
            print(f"\033[33mCurrent Value:\033[0m {old_val}")
            print("\033[90m(Type the exact code value, e.g. True, 0.5, or \"New String\")\033[0m")
            
            new_val = input("\n\033[32mNew Value (Press Enter to cancel):\033[0m ").strip()
            if new_val:
                text = text[:m.start('val')] + new_val + text[m.end('val'):]
                with open(file_path, 'w', encoding='utf-8') as f: f.write(text)
                print(f"\n\033[32m[SUCCESS]\033[0m {var_name} updated successfully!")
                import time; time.sleep(1)

    except ValueError:
        pass