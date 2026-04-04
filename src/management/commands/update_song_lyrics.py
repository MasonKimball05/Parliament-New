"""
Management command to update song lyrics from the Beta Theta Pi Song Book.
Clean lyrics extracted from "Songs of Beta Theta Pi Fraternity" (Revised 2005).

Usage:
    python manage.py update_song_lyrics
    python manage.py update_song_lyrics --dry-run  # Preview without changes
    python manage.py update_song_lyrics --fix-creator "Mason Kimball"  # Update creator
"""
from django.core.management.base import BaseCommand
from src.models import Song, ParliamentUser


# Clean lyrics from "Songs of Beta Theta Pi Fraternity" (Beta Tunes) - Revised 2005
SONG_LYRICS = {
    "The Alumni's Return": """We are singing again in the dear Old Hall
Of Beta Theta Pi.
Where oft we met to sing these songs
In golden days gone by.

CHORUS
Singing to-night, we are singing to-night,
Singing in the dear Old Hall,
Singing to-night, we are singing to-night,
Singing in the dear Old Hall.

The altar's light burns as bright to-night
As e'er it burned of yore;
A refuge from life's battle edge,
A home when toil is o'er.

CHORUS

As we sing to-night in the mystic light
Our sorrows quickly fly;
And each brother's heart is bound anew
In Beta Theta Pi.

CHORUS

And when these happy hours are gone,
Our meetings here are o'er,
Each brother's heart shall feel new strength,
For "the battle of life" once more.

CHORUS""",

    "As Betas Now We Meet": """As Betas now we meet, a brother new to greet
By grasp of hand, by grasp of hand;
Oh! may our sacred fire kindle a new desire,
And true Greek love inspire for all our band.

Our splendid shield he bears, the wreath of old he wears
And diamond bright, and diamond bright,
Oh! may he ever gain pure and unsullied fame
For Beta's glorious name for truth and right.""",

    "The Banquet Hall": """Hark! Hark! give heed to Wooglin's call:
Thrice welcome to the banquet hall!
The feast is spread, the wine is poured (wine is poured),
Come, gather 'round the festal board.

CHORUS
And drink, drink, drink, drink, drink your nectar rare!
Drink, drink, drink, drink, banish all your care!
In rare old Wine of Thirty-Nine,
Pledge: Grand old Beta, yours and mine!

Before this joyous night is gone,
Before our last good song is sung,
And while the lamps are growing dim (growing dim),
We'll strike our glasses brim to brim.

CHORUS""",

    "Behold the Mystic Symbol": """Thus heart to heart and hand to hand,
Each other's joy and grief to share;
Behold how Beta brothers stand,
And read our mystic symbol there.

Haste thee, oh Theta's golden age
Of cultured thought and lettered brain;
Hail bloodless conqueror and sage,
Monarch of mind, forever reign.

Behold at last the symbol mete
That binds our hearts with mystic tie;
Thus Wooglin's legend stands complete,
Beta to Theta linked with Pi.""",

    "The Beta Chorus": """Come, brothers, swell the Beta chorus,
Lift your voices loud in song,
Singing praise to good old Wooglin,
Wake the echoes loud and long!

And then we'll send the echoes to the heavens,
Where Beta stars are in the sky -
Then sing Phi Kai Phi, for Beta Theta Pi,
That the diamond's ray may light our way forever!""",

    "Beta Day": """Beta Day is dawning
You can see it in the sky,
It is bursting forth with friendship
For all Betas, You and I;
Sing forth my Beta Brothers,
Let your voices rise on high,
For Beta Theta Pi!

CHORUS
Yes, oh yes, we are all Betas,
Yes, oh yes, we are all Betas,
Yes, we are all loyal Betas,
We are linked in Phi Kai Phi.

We started out in Oxford,
Where they met beneath the elm,
Through the years we've passed the cup around
To build the dragon's realm,
The kindred love and friendship
That has grown along the way,
Inspires this Beta Day.

CHORUS

We have trod the porch at Mackinac
And Wooglin on the Lake
We have sung our songs at Bigwin
Strong fraternal bonds to make,
And now we gather once again,
With hand gripped firm in hand,
We are the Beta Band.

CHORUS

Then gather round my Brothers,
On this glorious Beta Day,
Live again old Wooglin's kindships
That can never fade away;
Oh greet your Beta Brothers
And renew those Beta ties,
Sing again your Phi Kai Phi's

CHORUS""",

    "Beta Doxology": """Bless now, O God on high,
Bless Beta Theta Pi;
Let naught of wrong
Sully our mystic gem,
Let not the wreath be dim;
Then shall praise be to Him
To whom our song.""",

    "The Beta Goodnight": """And now it's growing late,
And we'll have to say adieu
We'd love to hesitate,
Stay and sing some more to you.

Now close your drowsy eyes,
As we end this little theme.
We'll see you by and by,
In the Beta land of dreams.""",

    "Beta Hymn": """To Beta now our praises sing,
We lift our hearts in loyalty
To God who with us binds our hearts,
each brother's love shall ne'er depart
We all are bound, our song to thee,
In friendship's bond, fidelity.

To Wooglin's port we sound our cry,
Our Beta bond which shall not die
As cycles move we see afar
Our grand and glorious Beta stars
Our cause defend, our purpose high
To Beta Theta linked with Pi.

As ages past and now today,
Forever trust and thus we pray
That He who knows all hearts within,
Shall add each day His chosen men,
Those who would walk in Beta's light,
And Pledge to Beta Theta Pi.""",

    "Beta Lullaby": """I'm gonna rock-a-bye my baby to a Beta lullaby
And bring her up on Beta lore and Beta Theta Pi,
And when the dragon moon is shinin'
And those stars are in the sky
You can always hear me pinin'
For those carefree days gone by.

I'm gonna occupy those old porch chairs and
sing to Phi Kai Phi
While the Beta stars are shinin' in the sky
the Beta sky,
And when that ev'nin' sun goes down
We're gonna pass the lovin' cup around,
And rock-a-by my baby to a Beta lullaby

Rock-a-by baby the sandman is nigh,
Rock-a-by, rock-a-by, rock-a-by, ra-da,
Shh shh baby's asleep.""",

    "The Beta Marseillaise": """Ye sons of Beta, raise your voices,
Join one and all to swell the song.
While ev'ry loyal heart rejoices
The sounding chorus to prolong,
The sounding chorus to prolong,
In grateful praise your voices blending
To her whose radiant badge we bear,
And in whose mystic rites we share,
Worthy our grateful praise unending.

CHORUS
To Beta Theta Pi, a chorus ringing high,
A song, a song, full loud and long,
To Beta Theta Pi.

Extol in song fair Beta's glory,
Her noble aims, her purpose high.
Let brothers young, and brothers hoary,
Give praise to Beta Theta Pi,
Give praise to Beta Theta Pi!
Her tender love and care untiring,
The peerless honor of her name;
The splendor of her spotless fame,
In ev'ry heart her song inspiring.

CHORUS""",

    "The Beta Postscript": """Oh, when our sons to college go, to college go,
And We'll look them squarely in the eye, in the eye,
And say: "My boy, the only Greek you'll have to know
Is Beta, Beta Theta Pi!"

Oh! the Betas! yes, the Betas,
There is nothing else so great as
The fraternity your father joined in days of yore.

Adieu, adieu, my son, adieu, adieu, adieu!
For now it's plainly up to you, up to You,
To learn your Greek so well that you, my boy, and I
May know our Beta Theta Pi.""",

    "Beta Praise": """Brothers are we in Beta Theta Pi;
May kindred love between us ever be;
As life shall pass we hail our pledge to thee,
In Beta be our pride in Phi Kai Phi.

As Wooglin watches o'er his chosen men,
May Beta Spirit fill our hearts within.
When morning breaks, and earth's vain shadows fly,
Lift Wooglin's banner high, in Phi Kai Phi.

Blest be our cause in Brother and in Pledge;
Blest be this Chapter, keep her in Thy praise;
May Beta's Spirit always be our tie,
May Beta's light shine in Phi Kai Phi.""",

    "Beta Rose": """Queen of all the flowers that bloom,
Ruler of my heart.
Let us make a promise true,
Never more to part

CHORUS
Beta Rose, crimson rose,
When you smile at me,
Stars do sparkle in the sky,
Yet, not as bright as thee.

In a tree, I hear a bird,
Singing loud and clear,
In my heart, I hear a song,
Beta Rose my dear.

CHORUS

Beta rose, crimson rose,
Till eternity,
May we wander hand in hand,
In love and purity.""",

    "The Beta Shrine": """We come with heart and voice united,
With one accord our song we raise,
And wake the loud and sounding chorus,
Singing our fair old Beta's praise.
Here where we meet in bonds fraternal,
Here, where our sacred memories twine,
We bring with joy our choicest laurels
To lay, fair Beta, on thy shrine.

CHORUS
Then sing to Beta, fair old Beta!
Then sing and raise the chorus high!
Then hail! to Beta, fair old Beta!
Hail, hail, to Beta Theta Pi!

O Beta, thou art ever glorious,
Thy bonds are sweet, thy service joy!
The brightness of thy radiant image
Years shall not dim or time destroy.
Now, now to thee we bring our praises,
While we around thy altar bow;
Our loyal trust, our hearts' devotion,
Our love and faith we pledge thee now.""",

    "The Beta Stars": """When stars are hiding, and the moon is nowhere
in the sky;
And clouds are riding, and there's no light to guide
you by;
If you're a Beta, all along the way, the Beta stars
will make your darkness day;
For light or darkness, shine the stars of Beta Theta Pi.""",

    "Beta Sweetheart": """How would you like to be a Beta sweetheart?
How would you like to wear a Beta pin?
How would you like to gaze upon the diamond,
Gem of gems that ne'er grows dim?

How would you like to share a Beta friendship,
Friendship that will last through life?
How would you like to love a Beta always?
And how would you like to be a Beta wife?""",

    "Beta Sweetheart Song": """My Beta sweetheart
You will always be.
Soft as the starlight
Kissing the sea bewitching me.

We'll go on together
But if we may part.
You'll remain forever
Deep within my heart.

So wear this pin, my darling
It keeps my love only for you
My Beta sweetheart
You will always be
You will always be.""",

    "Beta's Emblems": """On Beta's night ev'ry heart is light,
Banished is care and sorrow;
We'll hear no sigh till the morn's grey eye
Fresh toil for itself doth borrow.

CHORUS
Then lift the song! Let it loud and long
Rise to Beta ever glorious!
Stainless and bright is her shield of light;
Her motto is: "Aye victorious."

Then strong are we in our mystic three
Whose emblems stand before us;
For truth and right we shout tonight,
Let each Beta join the chorus.

CHORUS

Trusting we stand, heart to heart, hand to hand,
The banner of truth waving o'er us;
To mutual need we give mutual heed,
And our pledge is ever before us.

CHORUS""",

    "Crew Song": """Heigh Ho, anybody home?
No drink, nor food, nor money have I none.
Still I will be merry anyhow
Since I joined the Beta Crew!

Heigh Ho, anybody home?""",

    "The Crow Song": """Three hungry Greeks went forth one day,
Vive la Theta Pi!
Three hungry Greeks went forth one day,
Vive la Theta Pi!
Three hungry Greeks went forth one day
To where old Wooglin holds his sway
And they all filled their lungs and cried:
Phi-Kai-Phi! Vive la Theta Pi!
And they all filled their lungs and cried:
Vive la Theta Pi!

Said one grim Greek unto his mates,
Vive la Theta Pi!
Said one grim Greek unto his mates,
Vive la Theta Pi!
Said one grim Greek unto his mates,
'Tis here there live two potentates,
And they all filled their lungs and cried:
Phi-Kai-Phi! Vive la Theta Pi!
And they all filled their lungs and cried:
Vive la Theta Pi!

They call them Wooglin and his dog,
Vive la Theta Pi!
They call them Wooglin and his dog,
Vive la Theta Pi!
They call them Wooglin and his dog,
The canine's fat as any hog,
And they all filled their lungs and cried:
Phi-Kai-Phi! Vive la Theta Pi!
And they all filled their lungs and cried:
Vive la Theta Pi!

If Wooglin comes forth with his beast,
Vive la Theta Pi!
If Wooglin comes forth with his beast,
Vive la Theta Pi!
If Wooglin comes forth with his beast,
We'll kill the pup and have a feast,
And they all filled their lungs and cried:
Phi-Kai-Phi! Vive la Theta Pi!
And they all filled their lungs and cried:
Vive la Theta Pi!

Then here's to Beta Theta Pi,
Vive la Theta Pi!
Then here's to Beta Theta Pi,
Vive la Theta Pi!
Then here's to Beta Theta Pi,
Fill up your lungs and give the cry!
And they all filled their lungs and cried:
Phi-Kai-Phi! Vive la Theta Pi!
And they all filled their lungs and cried:
Vive la Theta Pi!""",

    "Draw the Mystic Circle 'Round": """Then draw the mystic circle 'round,
Hearts and voices blending;
Let us pledge each other now
Friendship never ending.

Then chase away till coming day
The thought that we must sever,
And pledge to Beta Theta Pi
Fidelity forever.""",

    "For The Staunchest": """For the staunchest band of brothers,
Raise you hands on high
Test your strength against all others,
Beta Theta Pi

CHORUS
Hail the fairest; Hail O Beta;
Hail in Phi Kai Phi
Now the clan to us the closest;
Beta Theta Pi

When our college days are over
We will toast on high
Our fraternity beloved,
Beta Theta Pi.

CHORUS""",

    "The Froggie Song": """A little frog sat on the well,
on the well,
He said that here he'd like to dwell,
he'd like to dwell,
And so the chapter let him in,
and made a Beta Beta out of him.

Another frog sat on the well,
on the well,
He sang with fire in his eye,
oh, in his eye,
Said he, I am a chapter man,
In dear old Beta Beta Theta Pi.

Jim Dumps he leads a sorry life,
sorry life,
He has the meanest kind of wife,
oh, kind of wife,
His children would always get the croup,
and they would cry like Billy Roup.

At last quite driven to despair,
to despair,
Jim Dumps got up and tore his hair,
he tore his hair,
Until his wife brought home some force to him,
the force that made him Sunny Jim.

The Dean she leads a dirty life,
dirty life,
She eats potatoes with her knife,
oh, with her knife,
And when she takes her semi-annual scrub,
she leaves a ring around the tub –
The dirty Dean.""",

    "Gemma Nostra": """Gemma nostra candeat, obscurata nunquam,
Atque sertus conserat, caritatis unquam.

CHORUS
Salve! Beta Theta Pi, tu regina pura;
Cara tu meo cordi, cara, cara, cura.

Stella quisquay scintillet,
Sunt omnes aequales,
Nunc adsint si quillibet,
Internos sodales.

CHORUS

Inter fratres veritas,
Honor amicitia,
Fides, virtus jus et fas,
Omnes sint notitia.

CHORUS""",

    "I Took My Girl Out Walking": """I took my girl out walking late last Saturday night,
I took my girl out walking the moon was shining bright.
I asked her if she'd marry me and what do you think
she said
She said she would not marry me if the whole wide world
were dead.

CHORUS
That's why I do like I do like I do, my darling,
Do like I do like I do, my darling,
Do like I do like I do, my darling,
Do like I do like I do.

Last night I went to see her happy as could be,
Tonight she's out with another she cares no more for me;
So here's to a bottle of whiskey sparkling and so clear,
It's not as sweet as a young maid's kiss but a darn sight
more sincere.

CHORUS

Oh you must be a Beta, a Beta Theta Pi,
For if you are a Beta you'll be one till you die;
So sing your songs of Wooglin boys and raise your
voices high
For you must know the best of all is found in Phi Kai Phi.

CHORUS""",

    "In an Old Fashioned Garden": """In an old fashioned garden I found you
And lovely old flowers were there;
With their beauty and fragrance around you
But none could with you compare

As we stood in the twilight together
Each blossom our love did disclose;
Of each flower a part
You're the flower of my heart
And I called you my Beta Rose.""",

    "In the Old Porch Chairs": """When the shades of evening gather down around you,
String your old guitar and strum a tune or two;
There's your Alma Mater — "finest of the fine."
There's fair Beta — Beta, yours and mine.

When the pipes are glowing in the old porch chairs,
Plink your mandolin and plunk your chapter airs;
There's the "absent member" — she for whom you pine;
There's fair Beta - Beta, yours and mine.""",

    "The Jolly Greeks": """Barbarians we to college came,
Swedele dum bum;
But soon we learned to hate that name,
Swedele dum bum.
For slowly passed the unpleasant weeks,
Swedele tchu hirasa,
Until we joined the Jolly Greeks
Swedele dum bum.

CHORUS
Litoria, Litoria, swedele we tchu hirasa!
Litoria, Litoria swedele dum bum!

The tutors made us grub and dig,
Swedele dum bum;
The lessons tough, and deep and big,
Swedele dum bum.
But when we tasted college sweets,
Swedele tchu hirasa,
Was when we joined the Jolly Greeks,
Swedele dum bum.

CHORUS""",

    "Let All Stand Together": """Let all stand together — a band of true men
Vive la Theta Pi!
And help one another with hand, mouth and pen,
Vive la Theta Pi!

CHORUS
Vive la, vive la, vive la va
Vive la, vive la, vive la va
Vive la va, hop sa sa,
Vive la Theta Pi!

We'll merit the trust that our brothers repose,
Vive la Theta Pi!
And sooner will die than betray to their foes,
Vive la Theta Pi!

CHORUS

Thus honor shall come to the badge that we wear,
Vive la Theta Pi!
And every true Beta that honor shall share,
Vive la Theta Pi!

CHORUS""",

    "The Loving Cup": """Oh, start the loving cup around,
Nor pass a brother by;
We all drink from the same canteen
In Beta Theta Pi.
Oh, you and I can ne'er grow old
While this fair cup is nigh;
Here's life and strength,
Here's health and wealth,
Here's all in Phi-Kai-Phi.

Oh, start the loving cup around,
It speaks of other days;
We see the milestones backward run
When on this cup we gaze.
Our grip grows strong,
Bold comes our song
When this fair cup we raise,
So pass the loving cup around
And drink in Beta's praise.

Oh, start the loving cup around,
It holds a something clear,
'Tis brimming with a potion that
Will fill you with good cheer.
Come drink with me
And bid your ills
Forth-with to disappear;
We'll never in this world let fall
The cup we all hold here.""",

    "Marching Along": """Dreary the man who spurns his comrades,
Stumbling along his lonely way;
Happier he who joins his brothers'
Singing a Beta lay!

CHORUS
Marching along in Beta Theta Pi,
Marching along, we'll rend the air with song'
Strong in the might of our bond fraternal,
Friend of the right and the foe of the wrong!
Following paths old Wooglin blazed for us,
Till we arrive at thy shrine on high,
Singing again Mother of Men,
Hail to thee, Beta Theta Pi!

So in the night of care and sorrow,
Murky with clouds that shroud our way,
We will invoke a brighter morrow,
Singing a Beta lay!

CHORUS""",

    "My Beta Girl": """Night, and the fireside glowing,
Casting its spell over me;
Sitting there idly dreaming,
Thinking of one dear to me;
Seems that I see her there in the soft glow,
The sweetest of all I know.

CHORUS
Sweetheart of mine, my Beta girl,
Fairest of all, my Beta girl;
Eyes blue as skies of Beta blue,
Cheeks like the rose of Beta hue;
Tender and true, a pal to all;
Worthy of Beta's name;
You are my light, my diamond so bright,
My sweetheart, my Beta girl!

And then when I start dreaming,
Dreams bring back memories to me;
Mem'ries like moon-beams gleaming,
Shadows of my love to see;
She wears my diamond, my three stars of gold,
And this tells the story old.

CHORUS""",

    "Parting Song": """And now let hand grip into hand,
And eye look into eye,
As breaks the leal and loving band
Of Beta Theta Pi;
Of Beta Theta Pi, my boys,
Of Beta Theta Pi;
As breaks the leal and loving band
Of Beta Theta Pi.

The outside world is wrapped in sleep,
No barbaros is nigh,
As we these midnight vigils keep
Of Beta Theta Pi;
Of Beta Theta Pi, my boys,
Of Beta Theta Pi;
As we these midnight vigils keep
Of Beta Theta Pi.

And now let hand grip into hand
And eye look into eye,
As love flows free from heart to heart
in Beta Theta Pi;
In Beta Theta Pi, my boys,
In Beta Theta Pi;
As love flows free from heart to heart
In Beta Theta Pi.""",

    "Serenade Song": """And now it's growing late,
And it's time to say adieu,
We like to hesitate, stay and sing some more with you,
So close your drowsy eyes
As we end this little theme,
We'll see you bye and bye in the Beta land of dreams.""",

    "She Wears My Beta Pin": """Oh, she wears my Beta pin, yes, she wears my Beta pin;
She has a right to wear my Beta pin.
Stars that light the Beta skies, lend their lustre to her eyes,
Of course she has a right to wear my pin, my Beta pin.

When she wears my Beta pin, when she wears my Beta pin,
The Diamond's rarest hues flame from within;
O'er her heart the Shield of Gold tells a story very old,
You know she has a right to wear my pin, my Beta pin.""",

    "The Sons of the Dragon": """The dragon is lord of the beasts of the wold
And the ruler of birds of the air;
And Wooglin of old found him dauntless and bold,
As the guard of his secret lair.
Our pride and our dream is to keep him supreme
And we pledge him with hands raised high:
We're the sons of the dragon, and forevermore
We are guarding Beta Theta Pi!

The sons of the dragon are valiant and brave,
Ever ready to enter the fray;
Oh, what can assail or what can prevail
'Gainst the strength of the dragon's sway!
And strong in our might we go forward to fight
With the shout of our battle cry:
We're the sons of the dragon, and forevermore
We are guarding Beta Theta Pi!""",

    "Sweetheart Song": """Just put her in a corner,
And hold her tight like this,
Just put your arms around her waist,
And on her lips a kiss, if she'll let you;
And it she starts to murmur,
And if she starts to cry,
Just tell her it's the sacred seals, of
Beta Theta Pi.

Her eyes are blue as Beta skies,
Her cheeks are like a rose,
She's different from all other girls,
How I love her no one knows;
And in my fondest memories,
Never shall we part,
For she is my dear one, and I am her dear one,
She's my sweetheart in Phi Kai Phi.""",

    "There's a Scene": """There's a scene where brothers greet,
Where true kindred hearts do meet
At an altar sending love's sweet incense high,
Where is found without alloy,
Purest store of earthly joy;
'Tis within the halls of Beta Theta Pi.

CHORUS
Cheer! Cheer! Cheer! With hearts rejoicing!
Brightly sparkles ev'ry eye;
And our bosoms feel the glow
None but brothers' hearts may know,
While we sing the songs of Beta Theta Pi.

Friendship gave our order birth,
Pure and lasting as the earth;
Strong devotion to our motto gave us life;
With the help of brothers dear,
And of God, we've naught to fear,
As we mingle in the din of earthly strife.

CHORUS

Yes, and Beta girls there are,
Pure and lovely, passing fair,
Who with brightest smiles enliven all our way;
May our brothers ever prove
Worthy of such noble love,
Long as time shall last or earth shall have a day.

CHORUS""",

    "Ti-de-i-de-o": """Ti-de-i-de-o, ti-de-i-de-o, de-i, de-i, de-i, de-i-de-o
All the others take their hats off to us, boom,
Boom, boom,
We are the people so they say, so they say
Live on the shady side of Easy Street
And this is our night to shine, mm, mm, mm

Come along ye children, come along we say,
Boom boom, boom,
Come along the stars are shining bright,
Bright, bright, bright
Hop in our boat and we'll all take a float
For we're all out to have a good time, mm, mm, mm

We are the people, people, we are the people, people,
We are the people so they say, so they say;
We wear the diamond, we wear the diamond,
We wear the diamond and three stars.

Oh, you must be a Beta Theta Pi or you won't go
to Wooglin when you die, and why, cause,
We are the people, people, we are the people, people,
We are the people so they say, so they say;
We wear the diamond, we wear the diamond,
We wear the diamond and three stars.

De ump, de ump, de ump, jump, jump, jump, ish posh,
ice posh, i
Ice rosh a nice rosh, riff, raff, do faff, fang dang
a yellow bucket,
Ring dang doodle won't you kai, bo""",

    "To the Pledge": """The three bright stars are yours, my boy,
You're pledged to Beta's band;
Guard well your shield from life's alloy!
'Tis Wooglin's stern command.

And if old Wooglin sees you live
A life his praise can win,
You'll have the best that life can give,
You'll wear the Beta pin.

And when our circle folds you in,
Our Mysteries you'll know:
You'll feel the bond that makes us kin,
That sets our hearts aglow.

So steer your path towards Wooglin's den,
And with us sing his praise,
Until he makes you blest of men
For all your span of days.""",

    "We Are the People": """Ti-de-i-de-o, ti-de-i-de-o, de-i, de-i, de-i, de-i-de-o
All the others take their hats off to us, boom,
Boom, boom,
We are the people so they say, so they say
Live on the shady side of Easy Street
And this is our night to shine, mm, mm, mm

Come along ye children, come along we say,
Boom boom, boom,
Come along the stars are shining bright,
Bright, bright, bright
Hop in our boat and we'll all take a float
For we're all out to have a good time, mm, mm, mm

We are the people, people, we are the people, people,
We are the people so they say, so they say;
We wear the diamond, we wear the diamond,
We wear the diamond and three stars.

Oh, you must be a Beta Theta Pi or you won't go
to Wooglin when you die, and why, cause,
We are the people, people, we are the people, people,
We are the people so they say, so they say;
We wear the diamond, we wear the diamond,
We wear the diamond and three stars.""",

    "We Gather Again": """There's a legend among us, you know, brother,
That Wooglin only reigns
With those in these regions below, brother,
Who value the force of brains.
And he will not crown your brow, brother,
Unless in the battle's strife
By winning each victory now, brother,
You win in a bright way thro' life.

CHORUS
So, linked in our mystical chains brother,
We'll raise our fair banner on high;
And be true to old Wooglin, who reigns, brother,
In Beta Theta Pi

'Tis not by the fortunes of fate, brother,
That lasting fame is won;
They only are victors great, brother,
Who win ev'ry step they run.
Then keep our motto in view, brother,
And ever with lofty aim
Be fearless and pure, and true, brother,
And Wooglin will guard your fame.

CHORUS""",

    "Wooglin Forever!": """We are coming from the East, boys, we're coming
from the West,
Shouting "Old Wooglin forever,"
And the boys of sunny Southland are coming with
the rest,
Shouting "Old Wooglin forever!"

CHORUS
Wooglin forever! hurrah, boys, hurrah!
Long beam our Diamond and bright shine our Stars!
For we'll gather at the shrine, boys, we'll gather once
again,
Shouting "Old Wooglin forever!"

Here's a health to "Pater' Knox boys, and them of thirty-
nine,
Shouting "Old Wooglin forever!
And the sons that follow after them in long illustrious
line,
Shouting "Old Wooglin forever!

CHORUS

Our hearts and hands to Beta men, wherever they
may roam,
Light be their footsteps and ever
A kindly thought for us, boys, who still remain
at home,
Shouting "Old Wooglin forever!"

CHORUS""",

    "Wooglin Gives Us the Reason Why": """Sittin' in that hallowed hall, in eighteen thirty nine.
Eight men founded a fraternity, the greatest of all time.
One man named John Reily Knox, looked into their eyes.
He said "Brothers won't you sing with me?"
And this is what they cried:

CHORUS
Beta Theta Pi
Wooglin gives us the reason why.
Beta Theta Pi,
And still we all marched on.

One day we will be the best, is what he said back then.
Boy, if he could see us now, the stars would shine again.
Listen son, I'm passing down, the spirit that I know.
So throughout all your college days,
Your brotherhood will grow.

CHORUS

If you're wander'in down that path alone,
Just look up to the sky.
No brother ever walks alone,
those stars will be your guide.
My dear son come at my side,
Let hand grip into hand.
The dragon is our sovreign guide,
And ruler of this land.

CHORUS""",

    "Wooglin to the Pledge": """Come, smoke a friendly pipe with me
And drink my loyal ale,
Come, tilt a chair and loaf awhile
Against my fireside rail.
You'll feel a kind of something warm
Your marrow thro' and thro';
You'll feel a whole lot better off
When you're a Beta, too!

CHORUS
Hurrah! hurrah! come, drink of a Beta brew!
It's up to you to pledge anew, and join our jolly crew!
Hurrah! hurrah! come, drink of a Beta brew!
It's up to you to pledge anew, and join our jolly crew!

Dip in my old tobacco pouch
It holds the best, by far!
Take all you want, take all I have
Yes, take my last cigar.
And when a Beta offers you his hand You may be sure
His heart is in the bargain, too,
And all he has is yours.

CHORUS""",

    "You're the Girl of a Beta's Dreams": """Most days run along very much the same,
Each is filled with its joy and regret;
But the day that you came, and I first knew your name,
It's the day I can never forget.

Like an old romance where things come by chance,
Is the way that you came to me;
And I found delight in your smile so bright,
And you showed me what love could be.

Your eyes, your hair, and your face so fair,
Like an old masters painting it seems.
There is no one like you, there is no love to true;
You're the girl of a Beta's dreams.""",

    # Additional alternate title mappings for database songs
    "Thus Heart to Heart": """Thus heart to heart and hand to hand,
Each other's joy and grief to share;
Behold how Beta brothers stand,
And read our mystic symbol there.

Haste thee, oh Theta's golden age
Of cultured thought and lettered brain;
Hail bloodless conqueror and sage,
Monarch of mind, forever reign.

Behold at last the symbol mete
That binds our hearts with mystic tie;
Thus Wooglin's legend stands complete,
Beta to Theta linked with Pi.""",

    "My Beta Sweetheart": """My Beta sweetheart
You will always be.
Soft as the starlight
Kissing the sea bewitching me.

We'll go on together
But if we may part.
You'll remain forever
Deep within my heart.

So wear this pin, my darling
It keeps my love only for you
My Beta sweetheart
You will always be
You will always be.""",

    "Good Betas Sing Forever": """[Lyrics not available in this edition]""",

    "Ring the Bells of Old Miami": """[Lyrics not available in this edition]""",

    "We'll Always Hang Together": """[Lyrics not available in this edition]""",

    "I Love You, (Only You) Beta Girl": """[Lyrics not available - uses My Beta Girl melody]""",

    "My Beta Girl (You're the Only Girl)": """Night, and the fireside glowing,
Casting its spell over me;
Sitting there idly dreaming,
Thinking of one dear to me;
Seems that I see her there in the soft glow,
The sweetest of all I know.

CHORUS
Sweetheart of mine, my Beta girl,
Fairest of all, my Beta girl;
Eyes blue as skies of Beta blue,
Cheeks like the rose of Beta hue;
Tender and true, a pal to all;
Worthy of Beta's name;
You are my light, my diamond so bright,
My sweetheart, my Beta girl!

And then when I start dreaming,
Dreams bring back memories to me;
Mem'ries like moon-beams gleaming,
Shadows of my love to see;
She wears my diamond, my three stars of gold,
And this tells the story old.

CHORUS""",
}


class Command(BaseCommand):
    help = 'Update song lyrics from the Beta Theta Pi Song Book'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be updated without making changes',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update even if lyrics already exist',
        )
        parser.add_argument(
            '--fix-creator',
            type=str,
            help='Update created_by to specified user (by full name, e.g., "Mason Kimball")',
        )
        parser.add_argument(
            '--fix-creator-only',
            action='store_true',
            help='Only fix the creator field, do not update lyrics (use with --fix-creator)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']
        fix_creator = options.get('fix_creator')
        fix_creator_only = options.get('fix_creator_only')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made\n'))

        # Handle fix-creator option
        new_creator = None
        if fix_creator:
            # Try to find user by full name (name field)
            try:
                new_creator = ParliamentUser.objects.get(name__iexact=fix_creator.strip())
                self.stdout.write(self.style.SUCCESS(f'Found user: {new_creator.name} (ID: {new_creator.user_id})'))
            except ParliamentUser.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'User not found: {fix_creator}'))
                return
            except ParliamentUser.MultipleObjectsReturned:
                self.stdout.write(self.style.ERROR(f'Multiple users found with name: {fix_creator}'))
                return

        # Handle fix-creator-only mode
        if fix_creator_only:
            if not new_creator:
                self.stdout.write(self.style.ERROR('--fix-creator-only requires --fix-creator'))
                return

            songs = Song.objects.filter(is_active=True)
            updated_count = 0
            self.stdout.write(self.style.MIGRATE_HEADING('Fixing song creators...\n'))

            for song in songs:
                if dry_run:
                    self.stdout.write(f'  Would update creator for: {song.title}')
                else:
                    song.created_by = new_creator
                    song.save(update_fields=['created_by'])
                    self.stdout.write(self.style.SUCCESS(f'  Updated creator: {song.title}'))
                updated_count += 1

            self.stdout.write(self.style.MIGRATE_HEADING(f'\nUpdated creator for {updated_count} songs'))
            if dry_run:
                self.stdout.write(self.style.WARNING('DRY RUN - No changes were made.'))
            return

        # Title aliases for database titles that don't match SONG_LYRICS keys
        TITLE_ALIASES = {
            "As Beta Now We Meet": "As Betas Now We Meet",
            "Banquet Song": "The Banquet Hall",
            "The Beta Postscipt": "The Beta Postscript",
        }

        songs = Song.objects.filter(is_active=True)
        updated = 0
        not_found = 0
        skipped = 0

        self.stdout.write(self.style.MIGRATE_HEADING('Updating song lyrics...\n'))

        for song in songs:
            title = song.title

            # Check for title aliases first
            if title in TITLE_ALIASES:
                title = TITLE_ALIASES[title]

            # Try exact match first
            if title in SONG_LYRICS:
                lyrics = SONG_LYRICS[title]
            else:
                # Try without "The " prefix
                alt_title = title.replace('The ', '').strip()
                if alt_title in SONG_LYRICS:
                    lyrics = SONG_LYRICS[alt_title]
                # Try with "The " prefix
                elif f"The {title}" in SONG_LYRICS:
                    lyrics = SONG_LYRICS[f"The {title}"]
                else:
                    # Check for partial matches
                    lyrics = None
                    for key in SONG_LYRICS.keys():
                        if title.lower() in key.lower() or key.lower() in title.lower():
                            lyrics = SONG_LYRICS[key]
                            break

            if lyrics is None:
                self.stdout.write(f'  Not found: {song.title}')
                not_found += 1
                continue

            # Skip placeholder lyrics
            if '[Lyrics not available' in lyrics:
                self.stdout.write(f'  No lyrics available: {song.title}')
                skipped += 1
                continue

            # Check if lyrics already have content (unless force flag is set)
            if not force and song.lyrics and not song.lyrics.startswith('[Lyrics'):
                # Check if existing lyrics are garbled (many short lines indicate bad extraction)
                lines = [l for l in song.lyrics.split('\n') if l.strip()]
                if len(lines) > 5:
                    # Lyrics exist and seem substantial, skip unless force
                    self.stdout.write(f'  Already has lyrics: {song.title} (use --force to overwrite)')
                    skipped += 1
                    continue

            if dry_run:
                self.stdout.write(self.style.SUCCESS(f'  Would update: {song.title}'))
                preview = lyrics[:100].replace('\n', ' ')
                self.stdout.write(f'    Preview: {preview}...')
                if new_creator:
                    self.stdout.write(f'    Would set creator to: {new_creator.get_full_name()}')
            else:
                song.lyrics = lyrics.strip()
                if new_creator:
                    song.created_by = new_creator
                song.save()
                self.stdout.write(self.style.SUCCESS(f'  Updated: {song.title}'))

            updated += 1

        self.stdout.write(self.style.MIGRATE_HEADING('\nSummary:'))
        self.stdout.write(f'  Updated: {updated}')
        self.stdout.write(f'  Not found: {not_found}')
        self.stdout.write(f'  Skipped: {skipped}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were made. Run without --dry-run to update.'))
        else:
            self.stdout.write(self.style.SUCCESS('\nLyrics update complete!'))
