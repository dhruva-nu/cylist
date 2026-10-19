package cylist.app.session

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import androidx.core.content.edit
import android.security.keystore.KeyProperties
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Where the signed-in [Account] is kept between launches. One account at a time. */
interface SessionStore {
    /** The stored account, or null when there is none or it can no longer be read. */
    fun read(): Account?

    fun write(account: Account)

    fun clear()
}

/** Encrypts and decrypts small blobs. Throws [GeneralSecurityException] on a blob it did not seal. */
interface SecretBox {
    fun seal(plain: ByteArray): ByteArray

    fun open(sealed: ByteArray): ByteArray
}

/** One string-valued slot that survives the process. */
interface Slot {
    fun get(): String?

    fun put(value: String)

    fun remove()
}

/**
 * A [SessionStore] that seals the account with a [SecretBox] before it goes
 * into a [Slot], and reads back nothing it cannot open.
 *
 * On the phone the box is [KeystoreBox], whose key never leaves the Android
 * Keystore, and the slot is a private SharedPreferences file. What is on disk
 * is ciphertext; the token's plaintext exists only in memory.
 *
 * A blob that will not open (the Keystore key was wiped, the file was
 * corrupted, or it was written by a version that stored something else) is
 * removed, and the app starts signed out. Signing in again is the cure, so
 * there is nothing to report and nothing to retry.
 */
class SealedSessionStore(private val box: SecretBox, private val slot: Slot) : SessionStore {
    override fun read(): Account? {
        val stored = slot.get() ?: return null
        return try {
            val record = json.decodeFromString(Record.serializer(), String(box.open(Base64.getDecoder().decode(stored)), Charsets.UTF_8))
            if (record.version == VERSION) record.account else null
        } catch (_: GeneralSecurityException) {
            null
        } catch (_: IllegalArgumentException) {
            // Not base64, or JSON that does not decode to a Record:
            // kotlinx.serialization's SerializationException is one of these.
            null
        }.also { if (it == null) slot.remove() }
    }

    override fun write(account: Account) {
        val plain = json.encodeToString(Record.serializer(), Record(VERSION, account)).toByteArray(Charsets.UTF_8)
        slot.put(Base64.getEncoder().encodeToString(box.seal(plain)))
    }

    override fun clear() = slot.remove()

    @Serializable
    private data class Record(val version: Int, val account: Account)

    private companion object {
        /** Bump when [Account] changes shape in a way an older record cannot be read as. */
        const val VERSION = 1

        val json = Json { ignoreUnknownKeys = true }
    }
}

/**
 * AES-256-GCM with a key generated inside the Android Keystore, so the key
 * itself is never in the app's memory or on its disk. Each blob is its 12-byte
 * IV followed by the ciphertext and tag.
 *
 * The key needs no screen lock, so a session survives a reboot and is usable
 * before the first unlock; it is still bound to this install, and is
 * destroyed with the app's data.
 */
class KeystoreBox(private val alias: String = "cylist.session") : SecretBox {
    override fun seal(plain: ByteArray): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        return cipher.iv + cipher.doFinal(plain)
    }

    override fun open(sealed: ByteArray): ByteArray {
        if (sealed.size <= IV_BYTES) throw GeneralSecurityException("too short to be a sealed blob")
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, sealed, 0, IV_BYTES))
        return cipher.doFinal(sealed, IV_BYTES, sealed.size - IV_BYTES)
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keyStore.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_BITS)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val KEY_BITS = 256
        const val TAG_BITS = 128
        const val IV_BYTES = 12
    }
}

/**
 * A [Slot] in a private SharedPreferences file. Writes are committed before
 * they return, so a session saved just before the app is killed is there on
 * the next launch. Call it off the main thread.
 */
class PreferencesSlot(private val prefs: SharedPreferences, private val key: String = "session") : Slot {
    constructor(context: Context) : this(context.getSharedPreferences("cylist.session", Context.MODE_PRIVATE))

    override fun get(): String? = prefs.getString(key, null)

    override fun put(value: String) = prefs.edit(commit = true) { putString(key, value) }

    override fun remove() = prefs.edit(commit = true) { remove(key) }
}
