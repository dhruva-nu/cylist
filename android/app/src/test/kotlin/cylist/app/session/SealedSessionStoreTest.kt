package cylist.app.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Base64

class SealedSessionStoreTest {
    private val slot = MemorySlot()
    private val box = SoftwareBox()
    private val store = SealedSessionStore(box, slot)

    @Test
    fun readsBackWhatItWrote() {
        store.write(anAccount)

        assertEquals(anAccount, store.read())
        assertEquals(anAccount, SealedSessionStore(box, slot).read())
    }

    @Test
    fun keepsNoPlaintextOnDisk() {
        store.write(anAccount)

        val onDisk = slot.value!!
        assertFalse("cyl_phone" in onDisk)
        assertFalse("cyl_phone" in String(Base64.getDecoder().decode(onDisk), Charsets.ISO_8859_1))
        assertFalse("cylist.in" in onDisk)
    }

    @Test
    fun startsEmpty() = assertNull(store.read())

    @Test
    fun clearForgetsIt() {
        store.write(anAccount)
        store.clear()

        assertNull(store.read())
        assertNull(slot.value)
    }

    @Test
    fun aBlobSealedUnderAnotherKeyIsDroppedNotThrown() {
        SealedSessionStore(SoftwareBox(), slot).write(anAccount)

        assertNull(store.read())
        assertNull("an unreadable session is removed, so the next launch does not try again", slot.value)
    }

    @Test
    fun aTamperedBlobIsDropped() {
        store.write(anAccount)
        val bytes = Base64.getDecoder().decode(slot.value)
        bytes[bytes.size - 1] = (bytes[bytes.size - 1].toInt() xor 1).toByte()
        slot.value = Base64.getEncoder().encodeToString(bytes)

        assertNull(store.read())
        assertNull(slot.value)
    }

    @Test
    fun garbageIsDropped() {
        slot.value = "not base64 at all!"
        assertNull(store.read())
        assertNull(slot.value)

        slot.value = Base64.getEncoder().encodeToString(box.seal("""{"something": "else"}""".toByteArray()))
        assertNull(store.read())
        assertNull(slot.value)
    }

    @Test
    fun aRecordFromAnotherVersionIsDropped() {
        val record = """{"version": 99, "account": {"server": "https://cylist.in", "token": "cyl_x", "tokenId": "x",
            "tokenName": "x", "personName": "x"}}"""
        slot.value = Base64.getEncoder().encodeToString(box.seal(record.toByteArray()))

        assertNull(store.read())
    }

    @Test
    fun theTokenStaysOutOfToString() {
        assertFalse("cyl_phone" in anAccount.toString())
    }
}
