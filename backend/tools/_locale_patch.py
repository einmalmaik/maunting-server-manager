"""Einmalskript: Memory-Schluessel in allen elf Sprachdateien angleichen."""

import collections
import json
import pathlib

# Weg: der Bereichswechsler steckt jetzt im Scope-Prop des Panels statt in
# einem Dropdown darin.
ENTFERNEN = ("scope", "scopePersonal", "scopePersonalHint", "scopeTeamHint")

NEU = {
    "de": {
        "teamDescription": "Was der Assistent für dieses Team weiß. Alle Mitglieder sehen es; ändern darf es, wer den Schalter „Wissen pflegen“ hat.",
        "search": "Erinnerungen durchsuchen", "filterOrigin": "Nach Herkunft filtern",
        "origins": {"all": "Alle", "user": "Von mir", "ai": "Von der KI"},
        "count": "{{shown}} von {{total}} angezeigt", "noMatches": "Kein Eintrag passt zur Suche.",
        "add": "Hinzufügen", "edit": "Erinnerung bearbeiten", "clearAll": "Alle löschen",
        "clearTitle": "Gesamtes Gedächtnis löschen?",
        "clearConfirm": "Wirklich alle {{count}} Einträge löschen? Das lässt sich nicht rückgängig machen.",
        "cleared": "{{count}} Einträge gelöscht.",
    },
    "en": {
        "teamDescription": "What the assistant knows for this team. Every member can see it; changing it needs the “manage knowledge” switch.",
        "search": "Search memories", "filterOrigin": "Filter by origin",
        "origins": {"all": "All", "user": "From me", "ai": "From the AI"},
        "count": "Showing {{shown}} of {{total}}", "noMatches": "No entry matches the search.",
        "add": "Add", "edit": "Edit memory", "clearAll": "Delete all",
        "clearTitle": "Delete the entire memory?",
        "clearConfirm": "Really delete all {{count}} entries? This cannot be undone.",
        "cleared": "Deleted {{count}} entries.",
    },
    "es": {
        "teamDescription": "Lo que el asistente sabe de este equipo. Todos los miembros lo ven; cambiarlo requiere el permiso de gestionar conocimiento.",
        "search": "Buscar recuerdos", "filterOrigin": "Filtrar por origen",
        "origins": {"all": "Todos", "user": "De mí", "ai": "De la IA"},
        "count": "Mostrando {{shown}} de {{total}}", "noMatches": "Ninguna entrada coincide con la búsqueda.",
        "add": "Añadir", "edit": "Editar recuerdo", "clearAll": "Borrar todo",
        "clearTitle": "¿Borrar toda la memoria?",
        "clearConfirm": "¿Borrar realmente las {{count}} entradas? Esto no se puede deshacer.",
        "cleared": "{{count}} entradas borradas.",
    },
    "fr": {
        "teamDescription": "Ce que l’assistant sait de cette équipe. Tous les membres le voient ; le modifier requiert le droit de gérer les connaissances.",
        "search": "Rechercher dans les souvenirs", "filterOrigin": "Filtrer par origine",
        "origins": {"all": "Tous", "user": "De moi", "ai": "De l’IA"},
        "count": "{{shown}} sur {{total}} affichés", "noMatches": "Aucune entrée ne correspond à la recherche.",
        "add": "Ajouter", "edit": "Modifier le souvenir", "clearAll": "Tout supprimer",
        "clearTitle": "Supprimer toute la mémoire ?",
        "clearConfirm": "Supprimer vraiment les {{count}} entrées ? Cette action est irréversible.",
        "cleared": "{{count}} entrées supprimées.",
    },
    "pt": {
        "teamDescription": "O que o assistente sabe sobre esta equipe. Todos os membros veem; alterar exige a permissão de gerenciar conhecimento.",
        "search": "Pesquisar memórias", "filterOrigin": "Filtrar por origem",
        "origins": {"all": "Todos", "user": "De mim", "ai": "Da IA"},
        "count": "Mostrando {{shown}} de {{total}}", "noMatches": "Nenhuma entrada corresponde à pesquisa.",
        "add": "Adicionar", "edit": "Editar memória", "clearAll": "Excluir tudo",
        "clearTitle": "Excluir toda a memória?",
        "clearConfirm": "Excluir mesmo todas as {{count}} entradas? Isso não pode ser desfeito.",
        "cleared": "{{count}} entradas excluídas.",
    },
    "ru": {
        "teamDescription": "Что ассистент знает об этой команде. Видят все участники; изменять может тот, у кого есть переключатель управления знаниями.",
        "search": "Поиск по памяти", "filterOrigin": "Фильтр по источнику",
        "origins": {"all": "Все", "user": "От меня", "ai": "От ИИ"},
        "count": "Показано {{shown}} из {{total}}", "noMatches": "Ничего не найдено.",
        "add": "Добавить", "edit": "Изменить запись", "clearAll": "Удалить всё",
        "clearTitle": "Удалить всю память?",
        "clearConfirm": "Действительно удалить все записи ({{count}})? Это необратимо.",
        "cleared": "Удалено записей: {{count}}.",
    },
    "zh": {
        "teamDescription": "助手为该团队记住的内容。所有成员均可查看；修改需要“管理知识”开关。",
        "search": "搜索记忆", "filterOrigin": "按来源筛选",
        "origins": {"all": "全部", "user": "我添加的", "ai": "AI 记住的"},
        "count": "显示 {{shown}} / {{total}}", "noMatches": "没有匹配的条目。",
        "add": "添加", "edit": "编辑记忆", "clearAll": "全部删除",
        "clearTitle": "删除全部记忆？",
        "clearConfirm": "确定删除全部 {{count}} 条记录？此操作无法撤销。",
        "cleared": "已删除 {{count}} 条记录。",
    },
    "hi": {
        "teamDescription": "इस टीम के बारे में सहायक क्या जानता है। सभी सदस्य देख सकते हैं; बदलने के लिए ज्ञान प्रबंधन स्विच चाहिए।",
        "search": "यादें खोजें", "filterOrigin": "स्रोत से छाँटें",
        "origins": {"all": "सभी", "user": "मेरे द्वारा", "ai": "AI द्वारा"},
        "count": "{{total}} में से {{shown}} दिख रहे हैं", "noMatches": "खोज से कोई प्रविष्टि मेल नहीं खाती।",
        "add": "जोड़ें", "edit": "याद संपादित करें", "clearAll": "सब हटाएँ",
        "clearTitle": "पूरी स्मृति हटाएँ?",
        "clearConfirm": "क्या वाकई सभी {{count}} प्रविष्टियाँ हटाएँ? यह वापस नहीं किया जा सकता।",
        "cleared": "{{count}} प्रविष्टियाँ हटाई गईं।",
    },
    "bn": {
        "teamDescription": "এই দল সম্পর্কে সহকারী যা জানে। সব সদস্য দেখতে পারেন; পরিবর্তনের জন্য জ্ঞান ব্যবস্থাপনার অনুমতি লাগে।",
        "search": "স্মৃতি খুঁজুন", "filterOrigin": "উৎস অনুসারে ছাঁকুন",
        "origins": {"all": "সব", "user": "আমার দেওয়া", "ai": "AI-এর মনে রাখা"},
        "count": "{{total}}-এর মধ্যে {{shown}} দেখানো হচ্ছে", "noMatches": "অনুসন্ধানের সাথে কোনো এন্ট্রি মেলেনি।",
        "add": "যোগ করুন", "edit": "স্মৃতি সম্পাদনা", "clearAll": "সব মুছুন",
        "clearTitle": "পুরো স্মৃতি মুছবেন?",
        "clearConfirm": "সত্যিই সব {{count}}টি এন্ট্রি মুছবেন? এটি ফেরানো যাবে না।",
        "cleared": "{{count}}টি এন্ট্রি মোছা হয়েছে।",
    },
    "id": {
        "teamDescription": "Yang diketahui asisten tentang tim ini. Semua anggota bisa melihat; mengubah butuh izin mengelola pengetahuan.",
        "search": "Cari memori", "filterOrigin": "Saring menurut asal",
        "origins": {"all": "Semua", "user": "Dari saya", "ai": "Dari AI"},
        "count": "Menampilkan {{shown}} dari {{total}}", "noMatches": "Tidak ada entri yang cocok.",
        "add": "Tambah", "edit": "Ubah memori", "clearAll": "Hapus semua",
        "clearTitle": "Hapus seluruh memori?",
        "clearConfirm": "Benar-benar hapus semua {{count}} entri? Ini tidak bisa dibatalkan.",
        "cleared": "{{count}} entri dihapus.",
    },
    "ar": {
        "teamDescription": "ما يعرفه المساعد عن هذا الفريق. يراه جميع الأعضاء؛ ويتطلب التعديل صلاحية إدارة المعرفة.",
        "search": "البحث في الذكريات", "filterOrigin": "تصفية حسب المصدر",
        "origins": {"all": "الكل", "user": "مني", "ai": "من الذكاء الاصطناعي"},
        "count": "عرض {{shown}} من {{total}}", "noMatches": "لا يوجد إدخال مطابق للبحث.",
        "add": "إضافة", "edit": "تعديل الذكرى", "clearAll": "حذف الكل",
        "clearTitle": "حذف الذاكرة بالكامل؟",
        "clearConfirm": "هل تريد حقاً حذف جميع الإدخالات ({{count}})؟ لا يمكن التراجع.",
        "cleared": "تم حذف {{count}} إدخالاً.",
    },
}


def main() -> None:
    wurzel = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "locales"
    for sprache, neu in NEU.items():
        pfad = wurzel / f"{sprache}.json"
        daten = json.loads(pfad.read_text(encoding="utf-8"), object_pairs_hook=collections.OrderedDict)
        memory = daten["ai"]["memory"]
        for weg in ENTFERNEN:
            memory.pop(weg, None)
        for schluessel, wert in neu.items():
            memory[schluessel] = collections.OrderedDict(wert) if isinstance(wert, dict) else wert
        pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{sprache}: aktualisiert")


if __name__ == "__main__":
    main()
