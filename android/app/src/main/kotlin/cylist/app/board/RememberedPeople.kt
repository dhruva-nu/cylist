package cylist.app.board

import android.content.Context
import androidx.core.content.edit
import java.util.UUID

/** What is stored for "everyone": the empty pick (`board/boardPeople.ts`, `EVERYONE`). */
private const val EVERYONE = "everyone"

/**
 * Where the pick last made on each board is kept between launches, as the web
 * app keeps it in localStorage (CYLIST-110), in the same text form: `everyone`,
 * or comma-separated person ids.
 */
interface PickStore {
    fun read(projectKey: String): String?

    fun write(projectKey: String, pick: String)

    /** A store that remembers nothing, so every board opens on you. */
    object None : PickStore {
        override fun read(projectKey: String): String? = null

        override fun write(projectKey: String, pick: String) = Unit
    }
}

/** A [PickStore] in a private SharedPreferences file. */
class PreferencesPickStore(context: Context) : PickStore {
    private val prefs = context.applicationContext.getSharedPreferences("cylist.board", Context.MODE_PRIVATE)

    override fun read(projectKey: String): String? = prefs.getString(key(projectKey), null)

    override fun write(projectKey: String, pick: String) = prefs.edit { putString(key(projectKey), pick) }

    private fun key(projectKey: String) = "people.$projectKey"
}

/** The text that says [people] (`searchFromPeople`). */
fun pickText(people: Set<UUID>): String = if (people.isEmpty()) EVERYONE else people.joinToString(",")

/**
 * The pick a board opens on, from the one last made on it (`peopleFromSearch`).
 *
 * Somebody remembered who has since left the project is dropped; if nobody is
 * left, or nothing was ever picked, it is [me], if on this project. `everyone`
 * is the empty pick.
 */
fun openingPick(remembered: String?, me: UUID?, members: Set<UUID>): Set<UUID> {
    if (remembered == EVERYONE) return emptySet()
    if (remembered != null) {
        val stillHere = remembered.split(',').mapNotNull { runCatching { UUID.fromString(it) }.getOrNull() }
            .filter { it in members }.toSet()
        if (stillHere.isNotEmpty()) return stillHere
    }
    return if (me != null && me in members) setOf(me) else emptySet()
}
