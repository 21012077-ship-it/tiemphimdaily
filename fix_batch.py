import re

with open('templates/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')
print(f"Total lines before: {len(lines)}")

# ── Step 1: Find the container close (8-space </div>) at ~line 1070 ──
# We look for the pattern: empty line + 8-space-close + 4-space-close
# which is:  "\n\n        </div>\n    </div>\n"
# right after view-users ends

BATCH_HTML = '''            <!-- VIEW: LẤY HÀNG LOẠT -->
            <div id="view-batch" class="hidden">

                <div class="flex justify-between items-end mb-6 pb-4 border-b border-gray-100">
                    <div>
                        <h2 class="text-xl font-bold text-gray-800 flex items-center gap-2">
                            <i class="fas fa-list-check text-purple-500"></i> Lấy Tài Khoản Hàng Loạt
                        </h2>
                        <p class="text-sm text-gray-500 mt-1">Nhập danh sách email, lưu lại, sau đó bấm "Bắt Đầu Check" để check từng cái.</p>
                    </div>
                    <button onclick="batchSwitchView('input')" class="text-xs bg-purple-600 hover:bg-purple-700 text-white px-4 py-2 rounded-lg font-bold shadow transition-all flex items-center gap-1.5">
                        <i class="fas fa-plus"></i> Nhập Email Mới
                    </button>
                </div>

                <div id="batch-panel-input" class="hidden">
                    <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-6 mb-6">
                        <h3 class="text-sm font-bold text-gray-700 mb-3 flex items-center gap-2">
                            <i class="fas fa-envelope-open-text text-purple-400"></i> Dán danh sách email (mỗi dòng 1 email)
                        </h3>
                        <textarea id="batch-email-input" rows="10"
                            placeholder="example1@gmail.com&#10;example2@gmail.com&#10;..."
                            class="w-full bg-gray-50 border border-gray-200 rounded-xl px-4 py-3 text-sm font-mono text-gray-700 focus:outline-none focus:ring-2 focus:ring-purple-300 resize-y"></textarea>
                        <div class="flex gap-3 mt-4">
                            <button onclick="batchLoadEmails()" id="btn-batch-save"
                                class="bg-purple-600 hover:bg-purple-700 text-white px-6 py-2.5 rounded-lg font-bold text-sm shadow-md transition-all flex items-center gap-2">
                                <i class="fas fa-save"></i> Lưu Danh Sách
                            </button>
                            <button onclick="batchSwitchView('list')" class="bg-gray-100 hover:bg-gray-200 text-gray-700 px-5 py-2.5 rounded-lg font-semibold text-sm transition-all">
                                Huỷ
                            </button>
                        </div>
                    </div>
                </div>

                <div id="batch-panel-check" class="hidden mb-6">
                    <div class="bg-white rounded-2xl border-2 border-purple-200 shadow-lg p-6">
                        <div class="flex justify-between items-center mb-4">
                            <div class="text-sm font-bold text-gray-700">
                                Email <span id="batch-current-idx" class="text-purple-600">1</span>/<span id="batch-total-count" class="text-gray-500">0</span>
                            </div>
                            <div class="flex gap-2 text-xs">
                                <span class="bg-green-100 text-green-700 px-2 py-1 rounded-full font-bold" id="batch-count-usable">✓ 0</span>
                                <span class="bg-red-100 text-red-700 px-2 py-1 rounded-full font-bold" id="batch-count-expired">✗ 0</span>
                                <span class="bg-yellow-100 text-yellow-700 px-2 py-1 rounded-full font-bold" id="batch-count-fam">★ 0</span>
                            </div>
                        </div>
                        <div class="w-full bg-gray-200 rounded-full h-1.5 mb-5">
                            <div id="batch-progress-bar" class="bg-purple-500 h-1.5 rounded-full transition-all duration-300" style="width:0%"></div>
                        </div>
                        <div class="text-center mb-5">
                            <div class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-1">Email đang check</div>
                            <div id="batch-current-email" onclick="batchCopyCurrentEmail()" title="Click để copy"
                                class="text-xl font-black text-gray-900 font-mono bg-gray-50 px-4 py-3 rounded-xl border border-gray-200 break-all cursor-pointer hover:bg-purple-50 hover:border-purple-300 transition-colors"></div>
                            <div class="text-[10px] text-gray-400 mt-1.5"><i class="fas fa-copy mr-1"></i>Click vào email để copy</div>
                        </div>
                        <div class="flex flex-col sm:flex-row gap-3 mb-4">
                            <button onclick="batchMarkCurrent('expired')" class="flex-1 bg-red-50 hover:bg-red-500 hover:text-white border-2 border-red-300 text-red-700 font-bold py-3 rounded-xl transition-all flex items-center justify-center gap-2 text-sm">
                                <i class="fas fa-times-circle"></i> Hết Hạn
                            </button>
                            <button onclick="batchMarkCurrent('fam_unused')" class="flex-1 bg-yellow-50 hover:bg-yellow-500 hover:text-white border-2 border-yellow-300 text-yellow-700 font-bold py-3 rounded-xl transition-all flex items-center justify-center gap-2 text-sm">
                                <i class="fas fa-users-slash"></i> Fam Không Ai Sài
                            </button>
                            <button onclick="batchShowUsable()" class="flex-1 bg-green-500 hover:bg-green-600 text-white font-bold py-3 rounded-xl transition-all shadow-md flex items-center justify-center gap-2 text-sm">
                                <i class="fas fa-check-circle"></i> Sử Dụng Được
                            </button>
                        </div>
                        <div id="batch-usable-form" class="hidden bg-green-50 border border-green-200 rounded-xl p-4 mb-4">
                            <div class="text-xs font-bold text-green-700 mb-3"><i class="fas fa-info-circle"></i> Tuỳ chọn — điền hoặc bỏ trống</div>
                            <div class="flex flex-col sm:flex-row gap-3">
                                <div class="flex-1">
                                    <label class="text-xs text-gray-500 font-semibold mb-1 block">Tên Hồ Sơ</label>
                                    <input id="batch-profile-name" type="text" placeholder="VD: Phim HD" maxlength="50" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-300">
                                </div>
                                <div class="w-32">
                                    <label class="text-xs text-gray-500 font-semibold mb-1 block">PIN (nếu có)</label>
                                    <input id="batch-pin" type="text" placeholder="VD: 1234" maxlength="10" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-300">
                                </div>
                            </div>
                            <div class="flex gap-3 mt-3">
                                <button onclick="batchMarkCurrent('usable')" class="flex-1 bg-green-600 hover:bg-green-700 text-white font-bold py-2.5 rounded-lg transition-all text-sm shadow flex items-center justify-center gap-2">
                                    <i class="fas fa-save"></i> Xác Nhận &amp; Tiếp Theo
                                </button>
                                <button onclick="batchHideUsable()" class="bg-gray-200 hover:bg-gray-300 text-gray-700 font-semibold py-2.5 px-4 rounded-lg transition-all text-sm">Huỷ</button>
                            </div>
                        </div>
                        <div class="text-center">
                            <button onclick="batchSkipToEnd()" class="text-xs text-gray-400 hover:text-gray-600 transition-colors underline underline-offset-2">Dừng phiên này</button>
                        </div>
                    </div>
                </div>

                <div id="batch-panel-done" class="hidden mb-6">
                    <div class="bg-white rounded-2xl border-2 border-green-200 shadow-lg p-8 text-center">
                        <div class="w-16 h-16 bg-green-100 text-green-500 rounded-full flex items-center justify-center mx-auto mb-4 text-3xl"><i class="fas fa-check"></i></div>
                        <h3 class="text-lg font-bold text-gray-800 mb-1">Xong phiên này!</h3>
                        <p class="text-sm text-gray-500 mb-5" id="batch-done-summary"></p>
                        <button onclick="batchSwitchView('list')" class="bg-purple-600 hover:bg-purple-700 text-white px-6 py-2.5 rounded-lg font-bold text-sm shadow-md transition-all">Xem Danh Sách Đã Lưu</button>
                    </div>
                </div>

                <div id="batch-panel-list" class="hidden">
                    <div class="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
                        <div class="p-4 border-b border-gray-100 flex flex-wrap justify-between items-center gap-3">
                            <div class="flex items-center gap-3">
                                <h3 class="text-sm font-bold text-gray-700 flex items-center gap-2">
                                    <i class="fas fa-database text-purple-400"></i> Danh sách email
                                    <span id="batch-list-count" class="text-xs font-normal text-gray-400"></span>
                                </h3>
                                <button onclick="batchStartChecking()" id="btn-batch-start-check"
                                    class="bg-purple-600 hover:bg-purple-700 text-white text-xs font-bold px-4 py-1.5 rounded-lg shadow transition-all flex items-center gap-1.5">
                                    <i class="fas fa-play"></i> Bắt Đầu Check
                                </button>
                            </div>
                            <div class="flex gap-2">
                                <input id="batch-list-search" type="text" placeholder="Tìm email..." oninput="batchFilterList()"
                                    class="border border-gray-200 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-purple-300 w-48">
                                <select id="batch-list-filter" onchange="batchFilterList()" class="border border-gray-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none bg-white">
                                    <option value="">Tất cả</option>
                                    <option value="pending">Chưa check</option>
                                    <option value="usable">Sử dụng được</option>
                                    <option value="expired">Hết hạn</option>
                                    <option value="fam_unused">Fam không ai sài</option>
                                </select>
                            </div>
                        </div>
                        <div class="overflow-x-auto">
                            <table class="w-full text-sm">
                                <thead class="bg-gray-50 text-gray-500 text-xs uppercase font-bold tracking-wider border-b border-gray-200">
                                    <tr>
                                        <th class="px-4 py-3 text-left">Email <span class="text-gray-300 font-normal normal-case">(click để copy)</span></th>
                                        <th class="px-4 py-3 text-center">Trạng Thái</th>
                                        <th class="px-4 py-3 text-left">Hồ Sơ / PIN</th>
                                        <th class="px-4 py-3 text-center">Cooldown</th>
                                        <th class="px-4 py-3 text-center">Thao Tác</th>
                                    </tr>
                                </thead>
                                <tbody id="batch-list-tbody" class="divide-y divide-gray-50">
                                    <tr><td colspan="5" class="px-4 py-10 text-center text-gray-400 text-xs">Đang tải...</td></tr>
                                </tbody>
                            </table>
                        </div>
                        <div id="batch-list-empty" class="hidden py-12 text-center text-gray-400">
                            <i class="fas fa-inbox text-3xl mb-3 opacity-30"></i>
                            <p class="text-sm">Chưa có email nào. Nhập email mới để bắt đầu!</p>
                        </div>
                    </div>
                </div>

            </div><!-- end view-batch -->
'''

# ── Step 2: Remove ALL stray view-batch sections outside container ──
# The stray section has many ids duplicated (batch-panel-input, batch-list-tbody etc.)
# We'll remove everything between the second occurrence of id="view-batch" and its closing comment

# Find how many occurrences of id="view-batch" exist
count = content.count('id="view-batch"')
print(f'Occurrences of id="view-batch": {count}')

# Remove ALL occurrences of view-batch from the file first
# Pattern: <!-- VIEW: ... --> OR <div id="view-batch" ...> ... </div><!-- end view-batch -->
# We'll use line-by-line approach

# Find the 8-space container close (line 1070, 0-indexed 1069)
# It's after view-users section and before the modals
# The pattern is: line is exactly "        </div>" followed by "    </div>"

TARGET_LINE = '        </div>'
insert_idx = None
for i, line in enumerate(lines):
    stripped = line.rstrip('\r')
    if stripped == TARGET_LINE:
        # Check if it's around line 1070 (modals come right after)
        if i > 1000 and i < 1200:
            next_non_empty = None
            for j in range(i+1, min(i+10, len(lines))):
                if lines[j].strip().rstrip('\r'):
                    next_non_empty = lines[j].rstrip('\r')
                    break
            if next_non_empty and ('fixed inset-0' in next_non_empty or 'modalAddSlot' in next_non_empty or 'modal' in next_non_empty.lower()):
                insert_idx = i  # insert BEFORE this line
                print(f"Found container close at line {i+1}: {repr(stripped)}")
                print(f"  Next content: {repr(next_non_empty[:60])}")
                break

if insert_idx is None:
    print("ERROR: Could not find insertion point!")
    exit(1)

# ── Step 3: Remove all existing view-batch divs (stray ones) ──
# Build new content: collect lines, skip stray view-batch sections
new_lines = []
skip = False
skip_depth = 0
skipped_regions = []

i = 0
while i < len(lines):
    line = lines[i].rstrip('\r')
    
    # Check if this is a stray view-batch we should skip
    if 'id="view-batch"' in line and not skip:
        skip = True
        skip_depth = 1
        skipped_regions.append(i+1)
        print(f"  Skipping view-batch starting at line {i+1}")
        i += 1
        continue
    
    if skip:
        # Count div depth to find matching close
        skip_depth += line.count('<div') - line.count('</div')
        if '<!-- end view-batch -->' in line or (skip_depth <= 0 and '</div>' in line):
            skip = False
            skip_depth = 0
            print(f"  Finished skipping at line {i+1}")
        i += 1
        continue
    
    # At the insertion point, insert the batch HTML
    if i == insert_idx and not any(i+1 == r for r in skipped_regions):
        new_lines.append(BATCH_HTML)
        print(f"Inserted view-batch before line {i+1}")
    
    new_lines.append(line)
    i += 1

new_content = '\n'.join(new_lines)
print(f"\nNew content has {new_content.count(chr(10))+1} lines")
print(f'view-batch occurrences after: {new_content.count(\'id="view-batch"\') }')

with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Done! File written.")
