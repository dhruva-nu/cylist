package cylist.app.session

import cylist.api.AccountError
import cylist.api.AccountService
import cylist.api.MintedToken
import cylist.api.Revocation
import cylist.api.ServerAddress
import cylist.api.generated.models.Scope
import java.security.GeneralSecurityException
import java.security.SecureRandom
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** The same AES-GCM sealing as [KeystoreBox], with a key in memory instead of the Keystore. */
class SoftwareBox(private val key: SecretKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()) : SecretBox {
    override fun seal(plain: ByteArray): ByteArray {
        val iv = ByteArray(12).also(SecureRandom()::nextBytes)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(128, iv)) }
        return iv + cipher.doFinal(plain)
    }

    override fun open(sealed: ByteArray): ByteArray {
        if (sealed.size <= 12) throw GeneralSecurityException("too short")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, sealed, 0, 12)) }
        return cipher.doFinal(sealed, 12, sealed.size - 12)
    }
}

class MemorySlot(var value: String? = null) : Slot {
    override fun get(): String? = value

    override fun put(value: String) {
        this.value = value
    }

    override fun remove() {
        value = null
    }
}

class MemoryStore(var account: Account? = null) : SessionStore {
    override fun read(): Account? = account

    override fun write(account: Account) {
        this.account = account
    }

    override fun clear() {
        account = null
    }
}

/** An [AccountService] that answers from what each test sets, and records what it was asked. */
class FakeAccounts : AccountService {
    var probeFails: AccountError? = null
    var signInFails: AccountError? = null
    var revokeAnswer: () -> Revocation = { Revocation.Revoked }
    val probed = mutableListOf<ServerAddress>()
    val signIns = mutableListOf<Triple<String, String, List<Scope>>>()
    val revoked = mutableListOf<Pair<String, UUID>>()

    override fun probe(server: ServerAddress) {
        probed += server
        probeFails?.let { throw it }
    }

    override fun signIn(server: ServerAddress, email: String, password: String, tokenName: String, scopes: List<Scope>): MintedToken {
        signIns += Triple(email, tokenName, scopes)
        signInFails?.let { throw it }
        return MintedToken(server, "cyl_phone", TOKEN_ID, tokenName, "Aditi K", email)
    }

    override fun revoke(server: ServerAddress, token: String, tokenId: UUID): Revocation {
        revoked += token to tokenId
        return revokeAnswer()
    }

    companion object {
        val TOKEN_ID: UUID = UUID.fromString("0199a3c4-5b6e-7f80-9a1b-2c3d4e5f6a7b")
    }
}

fun address(url: String): ServerAddress =
    (ServerAddress.parse(url, setOf("10.0.2.2")) as ServerAddress.Parsed.Valid).address

val anAccount = Account(
    server = "https://cylist.in",
    token = "cyl_phone",
    tokenId = FakeAccounts.TOKEN_ID.toString(),
    tokenName = "Pixel 9 (Android)",
    personName = "Aditi K",
    personEmail = "aditi@example.com",
)
